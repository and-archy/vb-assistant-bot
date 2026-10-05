"""Свіжість даних alerts.in.ua. Історія тривог тягнеться за місяць
(`month_ago.json`), тож після відновлення API пропущене підтягнеться
саме — небезпечно лише, якщо прев'ю формується ПІД ЧАС збою: тоді
статистика ночі неповна, а бот раніше мовчки показував «спокійну ніч»
(збій `poll_alerts` лише логувався)."""

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from telegram.error import TelegramError
from telegram.ext import ContextTypes

from vb_assistant_bot import db
from vb_assistant_bot.config import Config

logger = logging.getLogger(__name__)

LAST_OK_KEY = "alerts_last_ok"
FAILING_SINCE_KEY = "alerts_failing_since"
NOTIFIED_KEY = "alerts_outage_notified"

# Скільки триває збій, перш ніж окремо повідомити адмінів (одиничні
# збої кожні 90 с — звична річ, через них турбувати не варто).
OUTAGE_NOTIFY_MINUTES = 30
# Якщо останнє успішне оновлення старше — у прев'ю попередження.
PREVIEW_STALE_MINUTES = 15


def _now() -> datetime:
    return datetime.now(UTC)


def _label(value: datetime, timezone: str) -> str:
    tz = ZoneInfo(timezone)
    local = value.astimezone(tz)
    if local.date() == datetime.now(tz).date():
        return f"{local:%H:%M}"
    return f"{local:%d.%m %H:%M}"


def _parse(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None


async def _notify(context: ContextTypes.DEFAULT_TYPE, config: Config, text: str) -> None:
    for admin_id in config.admin_user_ids:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except TelegramError as exc:
            logger.error("Не вдалося повідомити адміна %s про стан alerts.in.ua: %s", admin_id, exc)


async def on_poll_success(context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    now = _now()
    db.set_state(conn, LAST_OK_KEY, now.isoformat())
    failing_since = _parse(db.get_state(conn, FAILING_SINCE_KEY))
    if failing_since is None:
        return
    if db.get_state(conn, NOTIFIED_KEY):
        await _notify(
            context,
            config,
            "✅ Дані про тривоги з alerts.in.ua знову оновлюються "
            f"(збій тривав з {_label(failing_since, config.timezone)} до "
            f"{_label(now, config.timezone)}). Якщо ранкове прев'ю сформувалось під час "
            "збою — натисніть «🌅 Прев'ю зараз», щоб оновити статистику.",
        )
    db.delete_state(conn, FAILING_SINCE_KEY)
    db.delete_state(conn, NOTIFIED_KEY)


async def on_poll_failure(context: ContextTypes.DEFAULT_TYPE, error: Exception) -> None:
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    now = _now()
    failing_since = _parse(db.get_state(conn, FAILING_SINCE_KEY))
    if failing_since is None:
        failing_since = now
        db.set_state(conn, FAILING_SINCE_KEY, now.isoformat())
    if db.get_state(conn, NOTIFIED_KEY):
        return
    if now - failing_since < timedelta(minutes=OUTAGE_NOTIFY_MINUTES):
        return
    await _notify(
        context,
        config,
        f"⚠️ alerts.in.ua не відповідає з {_label(failing_since, config.timezone)} "
        f"({error}). Статистика ночі в прев'ю може бути неповною. "
        "Повідомлю, коли дані знову оновлюватимуться.",
    )
    db.set_state(conn, NOTIFIED_KEY, "1")


def stale_warning(conn, timezone: str) -> str | None:
    """Рядок-попередження для прев'ю, або None, якщо дані свіжі."""
    last_ok = _parse(db.get_state(conn, LAST_OK_KEY))
    if last_ok is None:
        return (
            "⚠️ Дані про тривоги ще жодного разу не завантажились — статистика може бути неповною."
        )
    if _now() - last_ok <= timedelta(minutes=PREVIEW_STALE_MINUTES):
        return None
    return (
        "⚠️ Дані про тривоги можуть бути неповними: alerts.in.ua не відповідає, "
        f"останнє успішне оновлення {_label(last_ok, timezone)}."
    )


def status_line(conn, timezone: str) -> str:
    last_ok = _parse(db.get_state(conn, LAST_OK_KEY))
    failing_since = _parse(db.get_state(conn, FAILING_SINCE_KEY))
    if last_ok is None:
        return "📡 Дані про тривоги: ще не завантажувались."
    if failing_since is not None and _now() - last_ok > timedelta(minutes=PREVIEW_STALE_MINUTES):
        return (
            f"📡 Дані про тривоги: ⚠️ alerts.in.ua не відповідає з "
            f"{_label(failing_since, timezone)} (останнє оновлення {_label(last_ok, timezone)})."
        )
    return f"📡 Дані про тривоги: оновлено {_label(last_ok, timezone)}."
