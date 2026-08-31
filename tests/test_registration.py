from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_registration_lifecycle(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "test.db",
        admin_password="test-password",
        secret_key="test-secret",
        public_base_url="https://lottery.example.com",
    )

    with TestClient(create_app(settings)) as client:
        assert client.post(
            "/api/admin/login", json={"password": "test-password"}
        ).status_code == 200

        created = client.post(
            "/api/admin/events",
            json={
                "title": "年会抽奖",
                "slug": "annual-party",
                "rounds": [
                    {"name": "一等奖", "prize": "旅行基金", "winner_count": 1}
                ],
            },
        )
        assert created.status_code == 201
        assert created.json()["join_url"] == "https://lottery.example.com/e/annual-party"

        registered = client.post(
            "/api/events/annual-party/participants", json={"name": "  张 三  "}
        )
        assert registered.status_code == 201
        assert registered.json()["name"] == "张 三"

        duplicate = client.post(
            "/api/events/annual-party/participants", json={"name": "张 三"}
        )
        assert duplicate.status_code == 409

        closed = client.patch(
            "/api/admin/events/annual-party/registration", json={"open": False}
        )
        assert closed.status_code == 200

        rejected = client.post(
            "/api/events/annual-party/participants", json={"name": "李四"}
        )
        assert rejected.status_code == 409
        assert rejected.json()["detail"] == "报名已关闭"