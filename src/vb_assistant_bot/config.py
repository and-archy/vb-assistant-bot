import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str
    admin_user_ids: frozenset[int]
    group_chat_id: int
    alerts_api_token: str
    alerts_region_uid: str
    db_path: str
    timezone: str
    texts_config_path: str
    thresholds_config_path: str
    auto_publish_enabled: bool


def _parse_admin_ids(raw: str) -> frozenset[int]:
    try:
        ids = frozenset(int(part) for part in raw.split(",") if part.strip())
    except ValueError as exc:
        raise RuntimeError(f"ADMIN_USER_IDS має бути csv-списком чисел, отримано: {raw!r}") from exc
    if not ids:
        raise RuntimeError("ADMIN_USER_IDS порожній після розбору")
    if len(ids) < 2:
        raise RuntimeError(
            "ADMIN_USER_IDS має містити щонайменше двох адмінів (ТЗ п.4 — "
            "щоб не залежати від однієї людини)"
        )
    return ids


def load_config() -> Config:
    load_dotenv()

    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN не встановлено (див. .env.example)")

    raw_admin_ids = os.environ.get("ADMIN_USER_IDS", "").strip()
    if not raw_admin_ids:
        raise RuntimeError("ADMIN_USER_IDS не встановлено (див. .env.example)")
    admin_ids = _parse_admin_ids(raw_admin_ids)

    raw_group_chat_id = os.environ.get("GROUP_CHAT_ID", "").strip()
    if not raw_group_chat_id:
        raise RuntimeError("GROUP_CHAT_ID не встановлено (див. .env.example)")
    try:
        group_chat_id = int(raw_group_chat_id)
    except ValueError as exc:
        raise RuntimeError(
            f"GROUP_CHAT_ID має бути числом, отримано: {raw_group_chat_id!r}"
        ) from exc

    alerts_api_token = os.environ.get("ALERTS_API_TOKEN", "").strip()
    if not alerts_api_token:
        raise RuntimeError("ALERTS_API_TOKEN не встановлено (див. .env.example)")

    alerts_region_uid = os.environ.get("ALERTS_REGION_UID", "31").strip()

    return Config(
        token=token,
        admin_user_ids=admin_ids,
        group_chat_id=group_chat_id,
        alerts_api_token=alerts_api_token,
        alerts_region_uid=alerts_region_uid,
        db_path=os.environ.get("DB_PATH", "vb_assistant.db"),
        timezone=os.environ.get("TIMEZONE", "Europe/Kyiv"),
        texts_config_path=os.environ.get("TEXTS_CONFIG_PATH", "config/texts.json"),
        thresholds_config_path=os.environ.get("THRESHOLDS_CONFIG_PATH", "config/thresholds.json"),
        auto_publish_enabled=os.environ.get("AUTO_PUBLISH_ENABLED", "false").strip().lower()
        in ("1", "true", "yes"),
    )
