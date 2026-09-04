from datetime import UTC, datetime


def parse_api_datetime(raw: str) -> datetime:
    """alerts.in.ua віддає ISO8601 з 'Z' (UTC) — datetime.fromisoformat
    до Python 3.11 не приймає 'Z', тож нормалізуємо вручну."""
    normalized = raw.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def to_canonical_utc_iso(raw: str) -> str:
    """Уніфікований формат для зберігання в БД (завжди +00:00, без
    мілісекунд/'Z') — щоб рядкове порівняння меж вікна ночі в
    db.alerts_overlapping було коректним."""
    return parse_api_datetime(raw).astimezone(UTC).isoformat()
