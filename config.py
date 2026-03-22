from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Set

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
SESSIONS_DIR = BASE_DIR / "sessions"
PROXIES_DIR = BASE_DIR / "proxies"
LOGS_DIR = BASE_DIR / "logs"
ARCHIVE_DIR = SESSIONS_DIR / "archive"
TEXTS_DIR = BASE_DIR / "texts"

for directory in (SESSIONS_DIR, PROXIES_DIR, LOGS_DIR, ARCHIVE_DIR, TEXTS_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def _parse_admin_ids(raw_value: str | None) -> Set[int]:
    if not raw_value:
        return set()
    values = [x.strip() for x in raw_value.split(",") if x.strip()]
    result: Set[int] = set()
    for item in values:
        try:
            result.add(int(item))
        except ValueError:
            continue
    return result


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    bot_token: str
    api_id: int | None
    api_hash: str
    crypto_bot_token: str
    trusted_admin_ids: Set[int]
    max_parallel_reports: int
    queue_workers: int
    proxy_mode: str
    dry_log_to_admin: bool
    validation_interval_sec: int
    support_form_headless: bool
    support_captcha_timeout_ms: int
    bot_api_timeout: float
    bot_api_force_ipv4: bool

    @property
    def telethon_enabled(self) -> bool:
        return bool(self.api_id) and bool(self.api_hash)


def load_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    api_id_str = os.getenv("API_ID", "").strip()
    trusted_ids_raw = os.getenv("TRUSTED_ADMIN_IDS") or os.getenv("TRUSTED_ADMIN_ID")

    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required. Create .env from .env.example and set BOT_TOKEN.")
    api_id_value: int | None = None
    if api_id_str:
        try:
            api_id_value = int(api_id_str)
        except ValueError:
            api_id_value = None

    return Settings(
        bot_token=bot_token,
        api_id=api_id_value,
        api_hash=api_hash,
        crypto_bot_token=os.getenv("CRYPTO_BOT_TOKEN", "").strip(),
        trusted_admin_ids=_parse_admin_ids(trusted_ids_raw),
        max_parallel_reports=int(os.getenv("MAX_PARALLEL_REPORTS", "5")),
        queue_workers=int(os.getenv("QUEUE_WORKERS", "3")),
        proxy_mode=os.getenv("PROXY_MODE", "rotate").lower(),
        dry_log_to_admin=os.getenv("DRY_LOG_TO_ADMIN", "1") == "1",
        validation_interval_sec=max(60, int(os.getenv("VALIDATION_INTERVAL_SEC", "1800"))),
        support_form_headless=os.getenv("SUPPORT_FORM_HEADLESS", "0") == "1",
        support_captcha_timeout_ms=max(30_000, int(os.getenv("SUPPORT_CAPTCHA_TIMEOUT_MS", "600000"))),
        bot_api_timeout=max(30.0, float(os.getenv("BOT_API_TIMEOUT", "120"))),
        bot_api_force_ipv4=_env_bool("BOT_API_FORCE_IPV4", sys.platform == "win32"),
    )
