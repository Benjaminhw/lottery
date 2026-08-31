from __future__ import annotations

import asyncio
import hmac
import json
import re
import secrets
import sqlite3
import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from random import SystemRandom
from urllib.parse import urlencode

import httpx
import qrcode
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse, RedirectResponse, StreamingResponse

from app.config import Settings
from app.db import connect, initialize, transaction
from app.wechat import WechatOAuthError, authorization_url, fetch_wechat_profile


SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
SECURE_RANDOM = SystemRandom()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def remember_participant(request: Request, slug: str, participant_id: int) -> None:
    participant_ids = dict(request.session.get("participant_ids", {}))
    participant_ids[slug] = participant_id
    request.session["participant_ids"] = dict(list(participant_ids.items())[-20:])


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class RoundInput(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    prize: str = Field(min_length=1, max_length=100)
    winner_count: int = Field(ge=1, le=500)

    @field_validator("name", "prize")
    @classmethod
    def clean_text(cls, value: str) -> str:
        cleaned = normalized_name(value)
        if not cleaned:
            raise ValueError("内容不能为空")
        return cleaned


class EventInput(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    slug: str = Field(min_length=2, max_length=40)
    rounds: list[RoundInput] = Field(min_length=1, max_length=30)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        cleaned = normalized_name(value)
        if not cleaned:
            raise ValueError("婚礼名称不能为空")
        return cleaned

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        value = value.lower().strip()
        if not SLUG_PATTERN.fullmatch(value):
            raise ValueError("婚礼代码只能使用小写字母、数字和连字符")
        return value


class RegistrationInput(BaseModel):
    open: bool


class ParticipantInput(BaseModel):
    name: str = Field(min_length=1, max_length=40)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = normalized_name(value)
        if not cleaned:
            raise ValueError("姓名不能为空")
        return cleaned


class RoundsInput(BaseModel):
    rounds: list[RoundInput] = Field(min_length=1, max_length=30)


def load_event_snapshot(database_path: Path, slug: str) -> dict[str, object] | None:
    with connect(database_path) as connection:
        event = connection.execute(
            "SELECT id, slug, title, registration_open, created_at FROM events WHERE slug = ?",
            (slug,),
        ).fetchone()
        if event is None:
            return None

        participant_rows = connection.execute(
            """
            SELECT id, name, source, avatar_url, created_at
            FROM participants
            WHERE event_id = ?
            ORDER BY created_at, id
            """,
            (event["id"],),
        ).fetchall()
        round_rows = connection.execute(
            """
            SELECT id, position, name, prize, winner_count, status, drawn_at
            FROM rounds
            WHERE event_id = ?
            ORDER BY position
            """,
            (event["id"],),
        ).fetchall()
        winner_rows = connection.execute(
            """
            SELECT w.round_id, w.position, p.id, p.name, p.source, p.avatar_url
            FROM winners w
            JOIN participants p ON p.id = w.participant_id
            WHERE w.event_id = ?
            ORDER BY w.round_id, w.position
            """,
            (event["id"],),
        ).fetchall()

    winners_by_round: dict[int, list[dict[str, object]]] = {}
    for row in winner_rows:
        winners_by_round.setdefault(row["round_id"], []).append(
            {
                "id": row["id"],
                "name": row["name"],
                "source": row["source"],
                "avatar_url": row["avatar_url"],
                "position": row["position"],
            }
        )

    participants = [
        {
            "id": row["id"],
            "name": row["name"],
            "source": row["source"],
            "avatar_url": row["avatar_url"],
            "created_at": row["created_at"],
        }
        for row in participant_rows
    ]
    rounds = [
        {
            "id": row["id"],
            "position": row["position"],
            "name": row["name"],
            "prize": row["prize"],
            "winner_count": row["winner_count"],
            "status": row["status"],
            "drawn_at": row["drawn_at"],
            "winners": winners_by_round.get(row["id"], []),
        }
        for row in round_rows
    ]
    return {
        "slug": event["slug"],
        "title": event["title"],
        "registration_open": bool(event["registration_open"]),
        "created_at": event["created_at"],
        "participant_count": len(participants),
        "participants": participants,
        "rounds": rounds,
    }


class EventStreamHub:
    def __init__(self, database_path: Path, wechat_enabled: bool) -> None:
        self.database_path = database_path
        self.wechat_enabled = wechat_enabled
        self._subscribers: dict[str, set[asyncio.Queue[str]]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._latest_messages: dict[str, str] = {}

    async def subscribe(self, slug: str) -> AsyncIterator[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        subscribers = self._subscribers.setdefault(slug, set())
        subscribers.add(queue)
        latest_message = self._latest_messages.get(slug)
        if latest_message is not None:
            queue.put_nowait(latest_message)
        task = self._tasks.get(slug)
        if task is None or task.done():
            self._tasks[slug] = asyncio.create_task(
                self._poll_event(slug), name=f"event-stream-{slug}"
            )

        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield message
                if message.startswith("event: error"):
                    return
        finally:
            subscribers = self._subscribers.get(slug)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(slug, None)
                    self._latest_messages.pop(slug, None)
                    task = self._tasks.pop(slug, None)
                    if task is not None:
                        task.cancel()

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        self._subscribers.clear()
        self._latest_messages.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _poll_event(self, slug: str) -> None:
        previous_payload: str | None = None
        try:
            while self._subscribers.get(slug):
                snapshot = await asyncio.to_thread(load_event_snapshot, self.database_path, slug)
                if snapshot is None:
                    self._publish(slug, 'event: error\ndata: {"detail":"婚礼不存在"}\n\n')
                    return
                snapshot["wechat_enabled"] = self.wechat_enabled
                payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
                if payload != previous_payload:
                    self._publish(slug, f"event: snapshot\ndata: {payload}\n\n")
                    previous_payload = payload
                await asyncio.sleep(1)
        finally:
            current_task = asyncio.current_task()
            if self._tasks.get(slug) is current_task:
                self._tasks.pop(slug, None)

    def _publish(self, slug: str, message: str) -> None:
        self._latest_messages[slug] = message
        for queue in tuple(self._subscribers.get(slug, ())):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(message)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    static_directory = Path(__file__).resolve().parent / "static"
    base_path = app_settings.base_path.rstrip("/")

    def app_path(path: str) -> str:
        return f"{base_path}{path}"

    event_stream_hub = EventStreamHub(
        app_settings.database_path,
        bool(app_settings.wechat_app_id and app_settings.wechat_app_secret),
    )
    if app_settings.environment == "production" and (
        app_settings.admin_password == "change-me"
        or app_settings.secret_key == "dev-only-change-this-secret"
    ):
        raise RuntimeError("生产环境必须设置安全的 ADMIN_PASSWORD 和 SECRET_KEY")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        initialize(app_settings.database_path)
        try:
            yield
        finally:
            await event_stream_hub.close()

    app = FastAPI(title="喜礼现场", version="0.1.0", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.wechat_profile_fetcher = fetch_wechat_profile
    app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=5)
    app.add_middleware(
        SessionMiddleware,
        secret_key=app_settings.secret_key,
        session_cookie="lottery_session",
        same_site="lax",
        https_only=app_settings.cookie_secure,
        max_age=60 * 60 * 12,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if app_settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    def require_admin(request: Request) -> None:
        if request.session.get("admin") is not True:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/admin/login")
    def login(payload: LoginRequest, request: Request) -> dict[str, bool]:
        if not hmac.compare_digest(payload.password, app_settings.admin_password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="密码错误")
        request.session.clear()
        request.session["admin"] = True
        return {"authenticated": True}

    @app.get("/api/admin/session")
    def admin_session(request: Request) -> dict[str, bool]:
        return {"authenticated": request.session.get("admin") is True}

    @app.post("/api/admin/logout")
    def logout(request: Request) -> dict[str, bool]:
        request.session.clear()
        return {"authenticated": False}

    @app.get("/api/admin/events")
    def list_events(_: None = Depends(require_admin)) -> list[dict[str, object]]:
        with connect(app_settings.database_path) as connection:
            rows = connection.execute(
                """
                SELECT e.slug, e.title, e.registration_open, e.created_at,
                       COUNT(DISTINCT p.id) AS participant_count,
                       COUNT(DISTINCT CASE WHEN r.status = 'drawn' THEN r.id END) AS drawn_rounds,
                       COUNT(DISTINCT r.id) AS round_count
                FROM events e
                LEFT JOIN participants p ON p.event_id = e.id
                LEFT JOIN rounds r ON r.event_id = e.id
                GROUP BY e.id
                ORDER BY e.created_at DESC
                """
            ).fetchall()
        return [
            {
                "slug": row["slug"],
                "title": row["title"],
                "registration_open": bool(row["registration_open"]),
                "created_at": row["created_at"],
                "participant_count": row["participant_count"],
                "drawn_rounds": row["drawn_rounds"],
                "round_count": row["round_count"],
            }
            for row in rows
        ]

    @app.post("/api/admin/events", status_code=status.HTTP_201_CREATED)
    def create_event(payload: EventInput, _: None = Depends(require_admin)) -> dict[str, object]:
        created_at = utc_now()
        try:
            with transaction(app_settings.database_path) as connection:
                cursor = connection.execute(
                    "INSERT INTO events (slug, title, created_at) VALUES (?, ?, ?)",
                    (payload.slug, payload.title, created_at),
                )
                event_id = cursor.lastrowid
                connection.executemany(
                    """
                    INSERT INTO rounds (event_id, position, name, prize, winner_count)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (event_id, index, item.name, item.prize, item.winner_count)
                        for index, item in enumerate(payload.rounds, start=1)
                    ],
                )
        except sqlite3.IntegrityError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="婚礼代码已存在") from error
        return {
            "slug": payload.slug,
            "title": payload.title,
            "registration_open": True,
            "join_url": f"{app_settings.public_base_url}/e/{payload.slug}",
        }

    @app.patch("/api/admin/events/{slug}/registration")
    def set_registration(
        slug: str, payload: RegistrationInput, _: None = Depends(require_admin)
    ) -> dict[str, bool]:
        with transaction(app_settings.database_path) as connection:
            cursor = connection.execute(
                "UPDATE events SET registration_open = ? WHERE slug = ?",
                (int(payload.open), slug),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="婚礼不存在")
        return {"registration_open": payload.open}

    @app.get("/api/admin/events/{slug}")
    def get_admin_event(slug: str, _: None = Depends(require_admin)) -> dict[str, object]:
        snapshot = load_event_snapshot(app_settings.database_path, slug)
        if snapshot is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="婚礼不存在")
        snapshot["join_url"] = f"{app_settings.public_base_url}/e/{slug}"
        snapshot["draw_url"] = f"{app_settings.public_base_url}/draw/{slug}"
        snapshot["qr_url"] = app_path(f"/api/events/{slug}/qr.png")
        return snapshot

    @app.put("/api/admin/events/{slug}/rounds")
    def replace_rounds(
        slug: str, payload: RoundsInput, _: None = Depends(require_admin)
    ) -> dict[str, object]:
        with transaction(app_settings.database_path) as connection:
            event = connection.execute("SELECT id FROM events WHERE slug = ?", (slug,)).fetchone()
            if event is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="婚礼不存在")
            has_results = connection.execute(
                "SELECT 1 FROM winners WHERE event_id = ? LIMIT 1", (event["id"],)
            ).fetchone()
            if has_results:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="已有抽奖结果，重置后才能修改轮次"
                )
            connection.execute("DELETE FROM rounds WHERE event_id = ?", (event["id"],))
            connection.executemany(
                """
                INSERT INTO rounds (event_id, position, name, prize, winner_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (event["id"], index, item.name, item.prize, item.winner_count)
                    for index, item in enumerate(payload.rounds, start=1)
                ],
            )
        snapshot = load_event_snapshot(app_settings.database_path, slug)
        assert snapshot is not None
        return snapshot

    @app.post("/api/events/{slug}/participants", status_code=status.HTTP_201_CREATED)
    def register_participant(
        slug: str, payload: ParticipantInput, request: Request
    ) -> dict[str, object]:
        with transaction(app_settings.database_path) as connection:
            event = connection.execute(
                "SELECT id, registration_open FROM events WHERE slug = ?", (slug,)
            ).fetchone()
            if event is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="婚礼不存在")
            if not event["registration_open"]:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="签到已关闭")
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO participants (event_id, name, name_key, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (event["id"], payload.name, payload.name.casefold(), utc_now()),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="该姓名已经签到"
                ) from error
        participant_id = cursor.lastrowid
        assert participant_id is not None
        remember_participant(request, slug, participant_id)
        return {"id": participant_id, "name": payload.name, "source": "manual"}

    @app.delete("/api/admin/events/{slug}/participants/{participant_id}")
    def delete_participant(
        slug: str, participant_id: int, _: None = Depends(require_admin)
    ) -> dict[str, bool]:
        with transaction(app_settings.database_path) as connection:
            event = connection.execute("SELECT id FROM events WHERE slug = ?", (slug,)).fetchone()
            if event is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="婚礼不存在")
            winner = connection.execute(
                "SELECT 1 FROM winners WHERE event_id = ? AND participant_id = ?",
                (event["id"], participant_id),
            ).fetchone()
            if winner:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="中奖者不能移出名单")
            cursor = connection.execute(
                "DELETE FROM participants WHERE id = ? AND event_id = ?",
                (participant_id, event["id"]),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="宾客不存在")
        return {"deleted": True}

    @app.get("/api/events/{slug}")
    def get_public_event(slug: str) -> dict[str, object]:
        snapshot = load_event_snapshot(app_settings.database_path, slug)
        if snapshot is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="婚礼不存在")
        snapshot["wechat_enabled"] = bool(
            app_settings.wechat_app_id and app_settings.wechat_app_secret
        )
        return snapshot

    @app.get("/api/events/{slug}/me")
    def get_current_participant(slug: str, request: Request) -> dict[str, object] | None:
        participant_id = request.session.get("participant_ids", {}).get(slug)
        if not participant_id:
            return None
        with connect(app_settings.database_path) as connection:
            row = connection.execute(
                """
                SELECT p.id, p.name, p.source, p.avatar_url
                FROM participants p
                JOIN events e ON e.id = p.event_id
                WHERE e.slug = ? AND p.id = ?
                """,
                (slug, participant_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "source": row["source"],
            "avatar_url": row["avatar_url"],
        }

    @app.get("/api/events/{slug}/qr.png")
    def event_qr_code(slug: str) -> Response:
        with connect(app_settings.database_path) as connection:
            event = connection.execute("SELECT 1 FROM events WHERE slug = ?", (slug,)).fetchone()
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="婚礼不存在")

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_Q,
            box_size=10,
            border=3,
        )
        qr.add_data(f"{app_settings.public_base_url}/e/{slug}")
        qr.make(fit=True)
        image = qr.make_image(fill_color="#281d1d", back_color="#fffafa")
        output = BytesIO()
        image.save(output, format="PNG")
        return Response(
            content=output.getvalue(),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/events/{slug}/stream")
    async def event_stream(slug: str) -> StreamingResponse:
        return StreamingResponse(
            event_stream_hub.subscribe(slug),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/auth/wechat/start")
    def start_wechat_oauth(event: str, request: Request) -> RedirectResponse:
        if not app_settings.wechat_app_id or not app_settings.wechat_app_secret:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="微信登录未配置")
        with connect(app_settings.database_path) as connection:
            event_row = connection.execute(
                "SELECT registration_open FROM events WHERE slug = ?", (event,)
            ).fetchone()
        if event_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="婚礼不存在")
        if not event_row["registration_open"]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="签到已关闭")

        state_token = secrets.token_urlsafe(24)
        request.session["wechat_oauth"] = {"state": state_token, "event": event}
        callback_url = f"{app_settings.public_base_url}/auth/wechat/callback"
        return RedirectResponse(
            authorization_url(app_settings.wechat_app_id, callback_url, state_token),
            status_code=status.HTTP_302_FOUND,
        )

    def wechat_error_redirect(slug: str, message: str) -> RedirectResponse:
        query = urlencode({"wechat_error": message})
        return RedirectResponse(
            app_path(f"/e/{slug}?{query}"), status_code=status.HTTP_303_SEE_OTHER
        )

    @app.get("/auth/wechat/callback")
    async def wechat_oauth_callback(
        request: Request, code: str = "", state: str = ""
    ) -> RedirectResponse:
        oauth_session = request.session.pop("wechat_oauth", None)
        if not oauth_session or not hmac.compare_digest(oauth_session.get("state", ""), state):
            return wechat_error_redirect("", "授权状态已失效，请重试")
        slug = oauth_session["event"]
        if not code:
            return wechat_error_redirect(slug, "你已取消微信授权")

        try:
            profile = await app.state.wechat_profile_fetcher(
                app_settings.wechat_app_id, app_settings.wechat_app_secret, code
            )
        except (WechatOAuthError, httpx.HTTPError, ValueError):
            return wechat_error_redirect(slug, "微信授权失败，请改用姓名签到")

        with transaction(app_settings.database_path) as connection:
            event_row = connection.execute(
                "SELECT id, registration_open FROM events WHERE slug = ?", (slug,)
            ).fetchone()
            if event_row is None:
                return wechat_error_redirect(slug, "婚礼不存在")
            existing = connection.execute(
                "SELECT id FROM participants WHERE event_id = ? AND wechat_openid = ?",
                (event_row["id"], profile.openid),
            ).fetchone()
            if existing:
                participant_id = existing["id"]
            else:
                if not event_row["registration_open"]:
                    return wechat_error_redirect(slug, "签到已关闭")
                base_name = normalized_name(profile.nickname)[:40] or "微信用户"
                display_name = base_name
                suffix_index = 2
                while connection.execute(
                    "SELECT 1 FROM participants WHERE event_id = ? AND name_key = ?",
                    (event_row["id"], display_name.casefold()),
                ).fetchone():
                    suffix = f" ({suffix_index})"
                    display_name = f"{base_name[: 40 - len(suffix)]}{suffix}"
                    suffix_index += 1
                cursor = connection.execute(
                    """
                    INSERT INTO participants
                        (event_id, name, name_key, source, avatar_url, wechat_openid, created_at)
                    VALUES (?, ?, ?, 'wechat', ?, ?, ?)
                    """,
                    (
                        event_row["id"],
                        display_name,
                        display_name.casefold(),
                        profile.avatar_url,
                        profile.openid,
                        utc_now(),
                    ),
                )
                participant_id = cursor.lastrowid
                assert participant_id is not None

        remember_participant(request, slug, participant_id)
        return RedirectResponse(
            app_path(f"/e/{slug}?joined=wechat"), status_code=status.HTTP_303_SEE_OTHER
        )

    def winner_payload(connection: sqlite3.Connection, round_id: int) -> list[dict[str, object]]:
        rows = connection.execute(
            """
            SELECT p.id, p.name, p.source, p.avatar_url, w.position
            FROM winners w
            JOIN participants p ON p.id = w.participant_id
            WHERE w.round_id = ?
            ORDER BY w.position
            """,
            (round_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "source": row["source"],
                "avatar_url": row["avatar_url"],
                "position": row["position"],
            }
            for row in rows
        ]

    @app.post("/api/admin/events/{slug}/rounds/{round_id}/draw")
    def draw_round(
        slug: str, round_id: int, _: None = Depends(require_admin)
    ) -> dict[str, object]:
        with transaction(app_settings.database_path) as connection:
            round_row = connection.execute(
                """
                SELECT r.id, r.event_id, r.position, r.name, r.prize, r.winner_count, r.status
                FROM rounds r
                JOIN events e ON e.id = r.event_id
                WHERE e.slug = ? AND r.id = ?
                """,
                (slug, round_id),
            ).fetchone()
            if round_row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="轮次不存在")
            if round_row["status"] == "drawn":
                return {
                    "round_id": round_id,
                    "winners": winner_payload(connection, round_id),
                    "repeated": True,
                }

            earlier_pending = connection.execute(
                """
                SELECT 1 FROM rounds
                WHERE event_id = ? AND position < ? AND status = 'pending'
                LIMIT 1
                """,
                (round_row["event_id"], round_row["position"]),
            ).fetchone()
            if earlier_pending:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="请按轮次顺序抽奖")

            eligible = connection.execute(
                """
                SELECT p.id
                FROM participants p
                LEFT JOIN winners w
                  ON w.event_id = p.event_id AND w.participant_id = p.id
                WHERE p.event_id = ? AND w.id IS NULL
                ORDER BY p.id
                """,
                (round_row["event_id"],),
            ).fetchall()
            if len(eligible) < round_row["winner_count"]:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"剩余候选人不足，需要 {round_row['winner_count']} 人，当前 {len(eligible)} 人",
                )

            selected = SECURE_RANDOM.sample(eligible, round_row["winner_count"])
            drawn_at = utc_now()
            connection.executemany(
                """
                INSERT INTO winners (event_id, round_id, participant_id, position, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (round_row["event_id"], round_id, row["id"], index, drawn_at)
                    for index, row in enumerate(selected, start=1)
                ],
            )
            connection.execute(
                "UPDATE rounds SET status = 'drawn', drawn_at = ? WHERE id = ?",
                (drawn_at, round_id),
            )
            connection.execute(
                "UPDATE events SET registration_open = 0 WHERE id = ?",
                (round_row["event_id"],),
            )
            winners = winner_payload(connection, round_id)
        return {"round_id": round_id, "winners": winners, "repeated": False}

    @app.post("/api/admin/events/{slug}/reset-draws")
    def reset_draws(slug: str, _: None = Depends(require_admin)) -> dict[str, bool]:
        with transaction(app_settings.database_path) as connection:
            event = connection.execute("SELECT id FROM events WHERE slug = ?", (slug,)).fetchone()
            if event is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="婚礼不存在")
            connection.execute("DELETE FROM winners WHERE event_id = ?", (event["id"],))
            connection.execute(
                "UPDATE rounds SET status = 'pending', drawn_at = NULL WHERE event_id = ?",
                (event["id"],),
            )
        return {"reset": True}

    app.mount("/assets", StaticFiles(directory=static_directory), name="assets")
    page_content = (static_directory / "index.html").read_text(encoding="utf-8").replace(
        "__BASE_PATH__", base_path
    )

    def application_page() -> HTMLResponse:
        return HTMLResponse(page_content)

    app.add_api_route("/", application_page, include_in_schema=False)
    app.add_api_route("/admin", application_page, include_in_schema=False)
    app.add_api_route("/e/{slug}", application_page, include_in_schema=False)
    app.add_api_route("/draw/{slug}", application_page, include_in_schema=False)

    return app


app = create_app()
