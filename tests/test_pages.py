from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def page_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "pages.db",
        admin_password="test-password",
        secret_key="test-secret",
        public_base_url="https://lottery.example.com",
    )


def test_application_pages_and_assets_are_served(tmp_path: Path) -> None:
    with TestClient(create_app(page_settings(tmp_path))) as client:
        for path in ["/", "/admin", "/e/sample-event", "/draw/sample-event"]:
            response = client.get(path)
            assert response.status_code == 200
            assert "幸运现场" in response.text
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]

        javascript = client.get("/assets/app.js")
        assert javascript.status_code == 200
        assert "startDrawAnimation" in javascript.text
        assert javascript.headers["x-content-type-options"] == "nosniff"


def test_production_rejects_default_secrets(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="生产环境必须设置"):
        create_app(
            Settings(
                database_path=tmp_path / "production.db",
                admin_password="change-me",
                secret_key="dev-only-change-this-secret",
                public_base_url="https://lottery.example.com",
                cookie_secure=True,
                environment="production",
            )
        )