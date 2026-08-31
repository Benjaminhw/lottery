from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.wechat import WechatProfile


def test_qr_and_wechat_registration(tmp_path: Path) -> None:
    application = create_app(
        Settings(
            database_path=tmp_path / "wechat.db",
            admin_password="test-password",
            secret_key="test-secret",
            public_base_url="https://lottery.example.com",
            wechat_app_id="wx-test-app",
            wechat_app_secret="wechat-secret",
        )
    )

    async def fake_profile(_: str, __: str, code: str) -> WechatProfile:
        assert code == "wechat-code"
        return WechatProfile(
            openid="openid-001",
            nickname="微信嘉宾",
            avatar_url="https://example.com/avatar.jpg",
        )

    application.state.wechat_profile_fetcher = fake_profile
    with TestClient(application) as client:
        client.post("/api/admin/login", json={"password": "test-password"})
        client.post(
            "/api/admin/events",
            json={
                "title": "微信活动",
                "slug": "wechat-event",
                "rounds": [{"name": "一等奖", "prize": "礼物", "winner_count": 1}],
            },
        )

        qr_response = client.get("/api/events/wechat-event/qr.png")
        assert qr_response.status_code == 200
        assert qr_response.headers["content-type"] == "image/png"
        assert qr_response.content.startswith(b"\x89PNG\r\n\x1a\n")

        oauth_start = client.get(
            "/auth/wechat/start?event=wechat-event", follow_redirects=False
        )
        assert oauth_start.status_code == 302
        authorize_url = urlparse(oauth_start.headers["location"])
        authorize_query = parse_qs(authorize_url.query)
        assert authorize_url.netloc == "open.weixin.qq.com"
        assert authorize_query["appid"] == ["wx-test-app"]
        assert authorize_query["scope"] == ["snsapi_userinfo"]
        state = authorize_query["state"][0]

        callback = client.get(
            f"/auth/wechat/callback?code=wechat-code&state={state}",
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "/e/wechat-event?joined=wechat"

        current = client.get("/api/events/wechat-event/me").json()
        assert current == {
            "id": 1,
            "name": "微信嘉宾",
            "source": "wechat",
            "avatar_url": "https://example.com/avatar.jpg",
        }
        public_event = client.get("/api/events/wechat-event").json()
        assert public_event["wechat_enabled"] is True
        assert public_event["participant_count"] == 1
        assert "wechat_openid" not in public_event["participants"][0]