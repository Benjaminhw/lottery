import asyncio
from pathlib import Path

from app.main import EventStreamHub


def test_event_stream_subscribers_share_one_snapshot_poll(
    tmp_path: Path, monkeypatch
) -> None:
    calls = 0

    def fake_snapshot(_: Path, slug: str) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "slug": slug,
            "title": "子衿 & 清和的婚礼",
            "registration_open": True,
            "participant_count": 0,
            "participants": [],
            "rounds": [],
        }

    monkeypatch.setattr("app.main.load_event_snapshot", fake_snapshot)

    async def receive_snapshots() -> tuple[str, str]:
        hub = EventStreamHub(tmp_path / "wedding.db", wechat_enabled=False)
        first_stream = hub.subscribe("our-wedding")
        second_stream = hub.subscribe("our-wedding")
        try:
            return await asyncio.gather(anext(first_stream), anext(second_stream))
        finally:
            await first_stream.aclose()
            await second_stream.aclose()
            await hub.close()

    first_message, second_message = asyncio.run(receive_snapshots())

    assert calls == 1
    assert first_message == second_message
    assert first_message.startswith("event: snapshot")
    assert '"wechat_enabled":false' in first_message


def test_late_event_stream_subscriber_receives_cached_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    def fake_snapshot(_: Path, slug: str) -> dict[str, object]:
        return {
            "slug": slug,
            "title": "子衿 & 清和的婚礼",
            "registration_open": True,
            "participant_count": 0,
            "participants": [],
            "rounds": [],
        }

    monkeypatch.setattr("app.main.load_event_snapshot", fake_snapshot)

    async def receive_snapshots() -> tuple[str, str]:
        hub = EventStreamHub(tmp_path / "wedding.db", wechat_enabled=False)
        first_stream = hub.subscribe("our-wedding")
        second_stream = hub.subscribe("our-wedding")
        try:
            first_message = await anext(first_stream)
            second_message = await asyncio.wait_for(anext(second_stream), timeout=0.1)
            return first_message, second_message
        finally:
            await first_stream.aclose()
            await second_stream.aclose()
            await hub.close()

    first_message, second_message = asyncio.run(receive_snapshots())

    assert second_message == first_message