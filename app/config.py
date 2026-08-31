from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    admin_password: str
    secret_key: str
    public_base_url: str
    cookie_secure: bool = False
    wechat_app_id: str = ""
    wechat_app_secret: str = ""
    environment: str = "development"
    base_path: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        project_root = Path(__file__).resolve().parents[1]
        load_dotenv(project_root / ".env")
        configured_base_path = os.getenv("BASE_PATH", "").strip()
        base_path = (
            f"/{configured_base_path.strip('/')}"
            if configured_base_path.strip("/")
            else ""
        )
        return cls(
            database_path=Path(
                os.getenv("DATABASE_PATH", project_root / "data" / "lottery.db")
            ),
            admin_password=os.getenv("ADMIN_PASSWORD", "change-me"),
            secret_key=os.getenv("SECRET_KEY", "dev-only-change-this-secret"),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
            wechat_app_id=os.getenv("WECHAT_APP_ID", ""),
            wechat_app_secret=os.getenv("WECHAT_APP_SECRET", ""),
            environment=os.getenv("APP_ENV", "development").lower(),
            base_path=base_path,
        )
