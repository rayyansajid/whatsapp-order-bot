"""Settings read from the environment."""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@dataclass(frozen=True)
class Settings:
    data_dir: Path = DEFAULT_DATA_DIR
    db_path: str = "orders.db"
    verify_token: str = ""
    whatsapp_token: str = ""
    whatsapp_phone_id: str = ""
    whatsapp_app_secret: str = ""
    admin_password: str = ""
    owner_whatsapp_number: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            data_dir=Path(env("ORDERBOT_DATA_DIR") or DEFAULT_DATA_DIR),
            db_path=env("ORDERBOT_DB") or "orders.db",
            verify_token=env("VERIFY_TOKEN", ""),
            whatsapp_token=env("WHATSAPP_TOKEN", ""),
            whatsapp_phone_id=env("WHATSAPP_PHONE_ID", ""),
            whatsapp_app_secret=env("WHATSAPP_APP_SECRET", ""),
            admin_password=env("ADMIN_PASSWORD", ""),
            owner_whatsapp_number=env("OWNER_WHATSAPP_NUMBER", ""),
        )
