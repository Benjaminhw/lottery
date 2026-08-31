from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def configured_client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                database_path=tmp_path / "draw.db",
                admin_password="test-password",
                secret_key="test-secret",
                public_base_url="https://lottery.example.com",
            )
        )
    )


def test_draws_are_ordered_persistent_and_unique(tmp_path: Path) -> None:
    with configured_client(tmp_path) as client:
        client.post("/api/admin/login", json={"password": "test-password"})
        client.post(
            "/api/admin/events",
            json={
                "title": "发布会",
                "slug": "launch-night",
                "rounds": [
                    {"name": "幸运奖", "prize": "礼盒", "winner_count": 2},
                    {"name": "大奖", "prize": "手机", "winner_count": 2},
                ],
            },
        )
        for name in ["甲", "乙", "丙", "丁"]:
            response = client.post(
                "/api/events/launch-night/participants", json={"name": name}
            )
            assert response.status_code == 201

        event = client.get("/api/events/launch-night").json()
        first_round, second_round = event["rounds"]

        out_of_order = client.post(
            f"/api/admin/events/launch-night/rounds/{second_round['id']}/draw"
        )
        assert out_of_order.status_code == 409
        assert out_of_order.json()["detail"] == "请按轮次顺序抽奖"

        first_draw = client.post(
            f"/api/admin/events/launch-night/rounds/{first_round['id']}/draw"
        )
        assert first_draw.status_code == 200
        first_winner_ids = {winner["id"] for winner in first_draw.json()["winners"]}
        assert len(first_winner_ids) == 2

        repeated = client.post(
            f"/api/admin/events/launch-night/rounds/{first_round['id']}/draw"
        )
        assert repeated.json()["repeated"] is True
        assert {winner["id"] for winner in repeated.json()["winners"]} == first_winner_ids

        second_draw = client.post(
            f"/api/admin/events/launch-night/rounds/{second_round['id']}/draw"
        )
        assert second_draw.status_code == 200
        second_winner_ids = {winner["id"] for winner in second_draw.json()["winners"]}
        assert len(second_winner_ids) == 2
        assert first_winner_ids.isdisjoint(second_winner_ids)

        snapshot = client.get("/api/events/launch-night").json()
        assert snapshot["registration_open"] is False
        assert [item["status"] for item in snapshot["rounds"]] == ["drawn", "drawn"]

        reset = client.post("/api/admin/events/launch-night/reset-draws")
        assert reset.status_code == 200
        reset_snapshot = client.get("/api/events/launch-night").json()
        assert all(item["status"] == "pending" for item in reset_snapshot["rounds"])
        assert all(not item["winners"] for item in reset_snapshot["rounds"])


def test_draw_requires_enough_people_and_admin_session(tmp_path: Path) -> None:
    with configured_client(tmp_path) as client:
        client.post("/api/admin/login", json={"password": "test-password"})
        client.post(
            "/api/admin/events",
            json={
                "title": "小型活动",
                "slug": "small-event",
                "rounds": [{"name": "奖项", "prize": "礼物", "winner_count": 2}],
            },
        )
        round_id = client.get("/api/events/small-event").json()["rounds"][0]["id"]
        client.post("/api/admin/logout")

        unauthorized = client.post(
            f"/api/admin/events/small-event/rounds/{round_id}/draw"
        )
        assert unauthorized.status_code == 401

        client.post("/api/admin/login", json={"password": "test-password"})
        insufficient = client.post(
            f"/api/admin/events/small-event/rounds/{round_id}/draw"
        )
        assert insufficient.status_code == 409
        assert "剩余候选人不足" in insufficient.json()["detail"]