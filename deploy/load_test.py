from __future__ import annotations

import argparse
import asyncio
import math
import os
import statistics
import time
from dataclasses import dataclass

import httpx


@dataclass(slots=True)
class GuestResult:
    page_ms: float
    assets_ms: float
    api_ms: float
    stream_ms: float
    registration_ms: float
    total_ms: float


def elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * ratio) - 1)
    return ordered[index]


def latency_summary(results: list[GuestResult], field: str) -> str:
    values = [getattr(result, field) for result in results]
    return (
        f"p50={statistics.median(values):.0f}ms "
        f"p95={percentile(values, 0.95):.0f}ms max={max(values):.0f}ms"
    )


async def run_guest(
    base_url: str,
    slug: str,
    guest_number: int,
    start_gate: asyncio.Event,
    hold_seconds: float,
) -> GuestResult:
    timeout = httpx.Timeout(30.0)
    limits = httpx.Limits(max_connections=6, max_keepalive_connections=4)
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
    ) as client:
        await start_gate.wait()
        total_started = time.perf_counter()

        stage_started = time.perf_counter()
        page = await client.get(f"/e/{slug}")
        page.raise_for_status()
        page_ms = elapsed_ms(stage_started)

        stage_started = time.perf_counter()
        stylesheet, javascript = await asyncio.gather(
            client.get("/assets/styles.css"), client.get("/assets/app.js")
        )
        stylesheet.raise_for_status()
        javascript.raise_for_status()
        assets_ms = elapsed_ms(stage_started)

        stage_started = time.perf_counter()
        event, current_guest = await asyncio.gather(
            client.get(f"/api/events/{slug}"), client.get(f"/api/events/{slug}/me")
        )
        event.raise_for_status()
        current_guest.raise_for_status()
        api_ms = elapsed_ms(stage_started)

        stage_started = time.perf_counter()
        async with client.stream("GET", f"/api/events/{slug}/stream") as stream:
            stream.raise_for_status()
            async for line in stream.aiter_lines():
                if line.startswith("data:"):
                    break
            stream_ms = elapsed_ms(stage_started)

            stage_started = time.perf_counter()
            registration = await client.post(
                f"/api/events/{slug}/participants",
                json={"name": f"LOAD-{guest_number:04d}"},
            )
            registration.raise_for_status()
            registration_ms = elapsed_ms(stage_started)
            await asyncio.sleep(hold_seconds)

        return GuestResult(
            page_ms=page_ms,
            assets_ms=assets_ms,
            api_ms=api_ms,
            stream_ms=stream_ms,
            registration_ms=registration_ms,
            total_ms=elapsed_ms(total_started),
        )


def process_rss_kib(process_id: int) -> int:
    try:
        status = open(f"/proc/{process_id}/status", encoding="utf-8").read()
    except (FileNotFoundError, PermissionError):
        return 0
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    return 0


def process_cpu_seconds(process_id: int) -> float:
    try:
        process_stat = open(
            f"/proc/{process_id}/stat", encoding="utf-8"
        ).read()
    except (FileNotFoundError, PermissionError):
        return 0
    fields = process_stat.rsplit(")", maxsplit=1)[1].split()
    clock_ticks = os.sysconf("SC_CLK_TCK")
    return (int(fields[11]) + int(fields[12])) / clock_ticks


async def monitor_process(
    process_id: int, stop_event: asyncio.Event
) -> tuple[int, int, float, float]:
    baseline_rss = process_rss_kib(process_id)
    peak_rss = baseline_rss
    baseline_cpu = process_cpu_seconds(process_id)
    started_at = time.perf_counter()
    while not stop_event.is_set():
        peak_rss = max(peak_rss, process_rss_kib(process_id))
        await asyncio.sleep(0.05)
    wall_seconds = time.perf_counter() - started_at
    cpu_seconds = process_cpu_seconds(process_id) - baseline_cpu
    return baseline_rss, peak_rss, cpu_seconds, wall_seconds


async def cleanup_load_guests(
    admin: httpx.AsyncClient, slug: str
) -> int:
    event = await admin.get(f"/api/admin/events/{slug}")
    event.raise_for_status()
    load_guests = [
        participant
        for participant in event.json()["participants"]
        if participant["name"].startswith("LOAD-")
    ]
    for participant in load_guests:
        response = await admin.delete(
            f"/api/admin/events/{slug}/participants/{participant['id']}"
        )
        response.raise_for_status()
    return len(load_guests)


async def run(args: argparse.Namespace) -> int:
    admin_timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(
        base_url=args.base_url,
        timeout=admin_timeout,
        follow_redirects=True,
    ) as admin:
        login = await admin.post(
            "/api/admin/login", json={"password": args.admin_password}
        )
        login.raise_for_status()
        session_cookie = login.cookies.get("lottery_session")
        if session_cookie:
            admin.headers["Cookie"] = f"lottery_session={session_cookie}"
        event = await admin.get(f"/api/admin/events/{args.slug}")
        event.raise_for_status()
        snapshot = event.json()
        if snapshot["participant_count"] or any(
            round_item["status"] == "drawn" for round_item in snapshot["rounds"]
        ):
            raise RuntimeError("压测要求使用没有宾客和开奖结果的空白场次")

        await admin.patch(
            f"/api/admin/events/{args.slug}/registration", json={"open": True}
        )

        start_gate = asyncio.Event()
        stop_monitor = asyncio.Event()
        monitor = asyncio.create_task(
            monitor_process(args.server_pid, stop_monitor)
        )
        tasks = [
            asyncio.create_task(
                run_guest(
                    args.base_url,
                    args.slug,
                    guest_number,
                    start_gate,
                    args.hold_seconds,
                )
            )
            for guest_number in range(1, args.clients + 1)
        ]
        await asyncio.sleep(0)
        benchmark_started = time.perf_counter()
        start_gate.set()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        benchmark_seconds = time.perf_counter() - benchmark_started
        stop_monitor.set()
        baseline_rss, peak_rss, cpu_seconds, monitor_seconds = await monitor

        results = [result for result in outcomes if isinstance(result, GuestResult)]
        errors = [result for result in outcomes if isinstance(result, BaseException)]
        removed = await cleanup_load_guests(admin, args.slug)

    print(f"clients={args.clients} success={len(results)} errors={len(errors)}")
    print(
        f"wall={benchmark_seconds:.2f}s throughput={len(results) / benchmark_seconds:.1f} flows/s"
    )
    for field in (
        "page_ms",
        "assets_ms",
        "api_ms",
        "stream_ms",
        "registration_ms",
        "total_ms",
    ):
        if results:
            print(f"{field.removesuffix('_ms'):>12}: {latency_summary(results, field)}")
    print(
        f"server_rss={baseline_rss / 1024:.1f}MiB "
        f"peak={peak_rss / 1024:.1f}MiB "
        f"cpu={cpu_seconds / monitor_seconds * 100:.0f}% cleanup={removed}"
    )
    for error in errors[:5]:
        print(f"error: {type(error).__name__}: {error}")
    return 0 if not errors and removed == len(results) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="婚礼扫码全流程并发压测")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080/wedding")
    parser.add_argument("--slug", default="our-wedding")
    parser.add_argument("--clients", type=int, default=100)
    parser.add_argument("--hold-seconds", type=float, default=3.0)
    parser.add_argument("--server-pid", type=int, default=0)
    parser.add_argument(
        "--admin-password", default=os.environ.get("ADMIN_PASSWORD", "")
    )
    args = parser.parse_args()
    if not args.admin_password:
        parser.error("请通过 ADMIN_PASSWORD 环境变量或参数提供管理密码")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))