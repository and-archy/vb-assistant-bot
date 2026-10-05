"""Журнал дій (кнопка «🕘 Історія»): хто з адмінів що зробив і що бот
зробив сам."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from vb_assistant_bot import db
from vb_assistant_bot.access import admin_label

HISTORY_DAYS = 7
HISTORY_LIMIT = 30

_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "нд")


def log(conn, user_id: int | None, action: str) -> None:
    db.log_action(conn, datetime.now(UTC).isoformat(), user_id, action)


def format_history(conn, timezone: str) -> str:
    since = (datetime.now(UTC) - timedelta(days=HISTORY_DAYS)).isoformat()
    rows = db.recent_actions(conn, since, HISTORY_LIMIT)
    if not rows:
        return f"🕘 За останні {HISTORY_DAYS} днів дій не було."
    tz = ZoneInfo(timezone)
    lines = [f"🕘 Історія за {HISTORY_DAYS} днів (останні {len(rows)}, нові зверху):", ""]
    for row in rows:
        local = datetime.fromisoformat(row["at"]).astimezone(tz)
        when = f"{_WEEKDAYS[local.weekday()]} {local:%d.%m %H:%M}"
        who = admin_label(conn, row["user_id"]) or "🤖 бот"
        lines.append(f"{when} · {who} — {row['action']}")
    return "\n".join(lines)
