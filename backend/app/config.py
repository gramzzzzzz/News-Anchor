"""Application configuration (environment driven). Everything the owner may tune lives in the settings
registry (services/settings_service.py); this file only holds what must be known before the database is up."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _bool(v: str | None, default: bool) -> bool:
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    data_dir: Path
    database_url: str
    secret_key: bytes
    session_hours: int
    static_dir: Path | None
    cookie_secure: bool
    scheduler: bool            # run the background scheduler in this process
    user_agent: str
    shared_dir: Path           # files written by the db container (its memory figure)
    mediacloud_token: str      # optional; can also be set in Settings
    model_dir: Path            # where the embedding model is kept


def load_config() -> Config:
    data_dir = Path(os.environ.get("NA_DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    if os.environ.get("NA_SECRET_KEY"):
        key = os.environ["NA_SECRET_KEY"].encode()
    else:
        key_file = data_dir / "secret.key"
        if not key_file.exists():
            key_file.write_bytes(secrets.token_hex(32).encode())
            os.chmod(key_file, 0o600)
        key = key_file.read_bytes().strip()
    static = os.environ.get("NA_STATIC_DIR")
    default_static = Path(__file__).resolve().parent.parent / "static"
    return Config(
        data_dir=data_dir,
        database_url=os.environ.get("NA_DATABASE_URL", "postgresql://na:na@localhost:5432/na"),
        secret_key=key,
        session_hours=int(os.environ.get("NA_SESSION_HOURS", "168")),
        static_dir=Path(static) if static else (default_static if default_static.exists() else None),
        cookie_secure=_bool(os.environ.get("NA_COOKIE_SECURE"), False),
        scheduler=_bool(os.environ.get("NA_SCHEDULER"), True),
        user_agent=os.environ.get("NA_USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
        shared_dir=Path(os.environ.get("NA_SHARED_DIR", str(data_dir / "shared"))),
        mediacloud_token=os.environ.get("NA_MEDIACLOUD_TOKEN", "").strip(),
        model_dir=Path(os.environ.get("NA_MODEL_DIR", str(data_dir / "models"))),
    )
