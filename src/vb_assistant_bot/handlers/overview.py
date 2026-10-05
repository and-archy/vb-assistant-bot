"""Кнопки лише для перегляду: «📋 Статус», «🗓 Заплановані», «🕘 Історія»."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from vb_assistant_bot import alerts_health, audit, db, health, scheduler
from vb_assistant_bot.access import admin_label, ensure_admin
from vb_assistant_bot.config import Config
from vb_assistant_bot.content import Thresholds
from vb_assistant_bot.handlers import custom

logger = logging.getLogger(__name__)

_PREVIEW_STATUS = {
    "pending": "чекає рішення",
    "queued": "заплановано",
    "skipped": "пропущено",
    "published": "опубліковано",
    "auto_sent": "опубліковано автоматично",
    "deleted": "видалено з General",
}


def _hhmm(value: datetime, timezone: str) -> str:
    local = value.astimezone(ZoneInfo(timezone))
    return f"{local.hour}:{local.minute:02d}"


def _who(conn, user_id) -> str:
    name = admin_label(conn, user_id)
    return f" ({name})" if name else ""


def _day_line(conn, today, config: Config) -> str:
    day_type = scheduler.determine_day_type(conn, today)
    label = "вихідний" if day_type == "weekend" else "робочий"
    manual = db.get_manual_day(conn, today.isoformat())
    source = f"позначено вручну{_who(conn, manual['set_by'])}" if manual else "за календарем"
    return f"📅 Сьогодні: {label} день ({source})."


def _preview_line(conn, morning_key: str, config: Config) -> str:
    preview = db.get_preview(conn, morning_key)
    if preview is None:
        return "🌅 Ранкове прев'ю: сьогодні ще не формувалось."
    status = preview["status"]
    text = _PREVIEW_STATUS.get(status, status)
    if status == "queued" and preview["scheduled_at"]:
        when = _hhmm(datetime.fromisoformat(preview["scheduled_at"]), config.timezone)
        text += f" на {when}"
    if status in ("queued", "skipped", "published"):
        text += _who(conn, preview["resolved_by"])
    if status == "deleted":
        text += _who(conn, preview["actor_id"])
    verdict = "важка" if preview["triggered"] else "не визнана важкою"
    return f"🌅 Ранкове прев'ю: {text}; ніч {verdict}."


def _autopublish_line(config: Config, thresholds: Thresholds | None) -> str:
    if thresholds is None:
        return ""
    when = f"{thresholds.autopublish_time.hour}:{thresholds.autopublish_time.minute:02d}"
    if config.auto_publish_enabled:
        return f"⏰ Автопублікація: увімкнена — о {when} після важкої ночі, крім сб/нд."
    return f"⏰ Автопублікація: вимкнена — о {when} лише нагадування."


def _custom_line(conn, config: Config) -> str:
    rows = db.scheduled_custom_messages(conn)
    if not rows:
        return "📝 Заплановані свої повідомлення: немає."
    nearest = datetime.fromisoformat(rows[0]["scheduled_at"]).astimezone(ZoneInfo(config.timezone))
    return (
        f"📝 Заплановані свої повідомлення: {len(rows)} "
        f"(найближче {nearest:%d.%m} о {nearest.hour}:{nearest.minute:02d})."
    )


def _errors_line(bot_data: dict, config: Config) -> str:
    state = health.error_state(bot_data)
    if state is None:
        return "🛠 Помилки в боті: немає."
    return (
        f"🛠 Помилки в боті: ⚠️ {state['count']} з {_hhmm(state['first'], config.timezone)}, "
        f"остання о {_hhmm(state['last'], config.timezone)} — чекаю відновлення."
    )


def build_status(context: ContextTypes.DEFAULT_TYPE) -> str:
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    thresholds: Thresholds | None = context.bot_data.get("thresholds")
    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz)
    lines = [
        f"📋 Статус на {now:%d.%m} {now.hour}:{now.minute:02d}",
        "",
        _day_line(conn, now.date(), config),
        _preview_line(conn, now.date().isoformat(), config),
        _autopublish_line(config, thresholds),
        alerts_health.status_line(conn, config.timezone),
        _custom_line(conn, config),
        _errors_line(context.bot_data, config),
    ]
    return "\n".join(line for line in lines if line)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return
    await update.effective_message.reply_text(build_status(context))


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return
    await update.effective_message.reply_text(
        audit.format_history(context.bot_data["conn"], config.timezone)
    )


async def scheduled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Надсилає адміну свіжу копію кожного запланованого повідомлення з
    кнопками керування. Нова копія замінює попередню в цьому чаті як
    «живу» — подальші зміни (іншим адміном тощо) оновлюватимуть саме її."""
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return
    conn = context.bot_data["conn"]
    chat_id = update.effective_user.id
    tz = ZoneInfo(config.timezone)
    morning_key = datetime.now(tz).date().isoformat()

    preview = db.get_preview(conn, morning_key)
    rows = db.scheduled_custom_messages(conn)
    has_queued = preview is not None and preview["status"] == "queued"
    if not rows and not has_queued:
        await update.effective_message.reply_text("🗓 Запланованих публікацій немає.")
        return
    await update.effective_message.reply_text(
        f"🗓 Заплановано публікацій: {len(rows) + int(has_queued)}. Керуйте кнопками під кожною."
    )

    if has_queued:
        body, keyboard = scheduler.preview_view(conn, morning_key, config.timezone)
        try:
            sent = await context.bot.send_message(chat_id=chat_id, text=body, reply_markup=keyboard)
            db.add_preview_message(conn, morning_key, chat_id, sent.message_id)
        except TelegramError as exc:
            logger.error("Не вдалося надіслати заплановане прев'ю: %s", exc)
    for row in rows:
        body, keyboard = custom.message_view(conn, row, config.timezone)
        try:
            sent = await context.bot.send_message(chat_id=chat_id, text=body, reply_markup=keyboard)
            db.add_custom_message_preview(conn, row["id"], chat_id, sent.message_id)
        except TelegramError as exc:
            logger.error("Не вдалося надіслати своє повідомлення id=%s: %s", row["id"], exc)
