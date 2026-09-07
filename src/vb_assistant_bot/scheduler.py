import json
import logging
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from vb_assistant_bot import db, deck
from vb_assistant_bot.alerts_client import AlertsInUaClient
from vb_assistant_bot.config import Config
from vb_assistant_bot.content import Texts, Thresholds
from vb_assistant_bot.formatting import format_stats_summary
from vb_assistant_bot.message_builder import build_message
from vb_assistant_bot.night_logic import compute_night_stats, is_heavy_night
from vb_assistant_bot.timeutil import to_canonical_utc_iso

logger = logging.getLogger(__name__)

_DEFAULT_SET_WEEKDAY = "A"
_DEFAULT_SET_WEEKEND = "V"
_CALM_SET = "B"


def determine_day_type(conn, morning_date: date) -> str:
    manual = db.get_manual_day_type(conn, morning_date.isoformat())
    if manual is not None:
        return manual
    return "weekend" if morning_date.weekday() >= 5 else "workday"


def _preview_keyboard(morning_date: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Надіслати", callback_data=f"prev:{morning_date}:send")],
            [
                InlineKeyboardButton("Інший варіант", callback_data=f"prev:{morning_date}:more"),
                InlineKeyboardButton("Стриманий тон", callback_data=f"prev:{morning_date}:calm"),
            ],
            [
                InlineKeyboardButton(
                    "Пропустити сьогодні", callback_data=f"prev:{morning_date}:skip"
                )
            ],
        ]
    )


def register(application: Application, config: Config, thresholds: Thresholds) -> None:
    tz = ZoneInfo(config.timezone)
    application.job_queue.run_repeating(
        poll_alerts, interval=thresholds.alerts_poll_interval_seconds, first=1
    )
    application.job_queue.run_daily(job_preview, time=thresholds.preview_time.replace(tzinfo=tz))
    application.job_queue.run_daily(
        job_autopublish, time=thresholds.autopublish_time.replace(tzinfo=tz)
    )


async def poll_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    client: AlertsInUaClient = context.bot_data["alerts_client"]

    try:
        records = client.fetch_region_history(config.alerts_region_uid)
    except Exception:
        logger.exception("Не вдалося отримати тривоги з alerts.in.ua")
        return

    for record in records:
        if not record.external_id:
            continue
        db.upsert_alert(
            conn,
            external_id=record.external_id,
            location_uid=record.location_uid,
            raw_alert_type=record.raw_alert_type,
            threat_types=list(record.threat_types),
            started_at=to_canonical_utc_iso(record.started_at),
            finished_at=to_canonical_utc_iso(record.finished_at) if record.finished_at else None,
            updated_at=to_canonical_utc_iso(record.updated_at),
        )


async def generate_and_send_preview(context: ContextTypes.DEFAULT_TYPE, *, force: bool) -> bool:
    """Повертає True, якщо прев'ю надіслано. force=True — ручний /support,
    ігнорує результат тригера (ТЗ п.4)."""
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    texts: Texts = context.bot_data["texts"]
    thresholds: Thresholds = context.bot_data["thresholds"]

    tz = ZoneInfo(config.timezone)
    morning_date = datetime.now(tz).date()

    stats = compute_night_stats(
        conn,
        location_uid=config.alerts_region_uid,
        morning_date=morning_date,
        timezone=config.timezone,
        thresholds=thresholds,
    )
    triggered = is_heavy_night(stats, thresholds)
    if not triggered and not force:
        return False

    day_type = determine_day_type(conn, morning_date)
    default_set = _DEFAULT_SET_WEEKEND if day_type == "weekend" else _DEFAULT_SET_WEEKDAY
    variant = deck.draw_next(conn, default_set, texts.sets[default_set])
    include_arrangements = day_type == "workday"
    message_text = build_message(texts, variant, include_arrangements=include_arrangements)

    morning_key = morning_date.isoformat()
    db.upsert_preview(
        conn,
        morning_date=morning_key,
        status="pending",
        day_type=day_type,
        variant_set=default_set,
        variant_id=variant.id,
        message_text=message_text,
        stats_json=json.dumps({"count": stats.count, "triggered": triggered}),
        created_at=datetime.now(UTC).isoformat(),
    )

    intro = format_stats_summary(stats)
    body = f"{intro}\n\nГотовий текст:\n{message_text}"
    keyboard = _preview_keyboard(morning_key)
    for admin_id in config.admin_user_ids:
        try:
            sent = await context.bot.send_message(
                chat_id=admin_id, text=body, reply_markup=keyboard
            )
        except TelegramError as exc:
            logger.error("Не вдалося надіслати прев'ю адміну %s: %s", admin_id, exc)
            continue
        db.add_preview_message(conn, morning_key, admin_id, sent.message_id)

    return True


async def job_preview(context: ContextTypes.DEFAULT_TYPE) -> None:
    await generate_and_send_preview(context, force=False)


async def job_autopublish(context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    tz = ZoneInfo(config.timezone)
    morning_key = datetime.now(tz).date().isoformat()

    preview = db.get_preview(conn, morning_key)
    if preview is None or preview["status"] != "pending":
        return

    if not config.auto_publish_enabled:
        note = (
            "⏰ Прев'ю на сьогодні досі без реакції. Автопублікація вимкнена "
            "(AUTO_PUBLISH_ENABLED=false — перший місяць лише ручний режим, ТЗ п.13). "
            "Натисніть кнопку вище або викличте /support."
        )
        for admin_id in config.admin_user_ids:
            try:
                await context.bot.send_message(chat_id=admin_id, text=note)
            except TelegramError as exc:
                logger.error("Не вдалося надіслати нагадування адміну %s: %s", admin_id, exc)
        return

    await _publish(context, preview["message_text"], morning_key)
    db.resolve_preview(conn, morning_key, "auto_sent", None, datetime.now(UTC).isoformat())
    db.add_send_log(
        conn,
        morning_date=morning_key,
        variant_set=preview["variant_set"],
        variant_id=preview["variant_id"],
        mode="auto",
        sent_by=None,
        sent_at=datetime.now(UTC).isoformat(),
        group_message_id=None,
    )
    await _clear_preview_messages(context, conn, morning_key)


async def _publish(context: ContextTypes.DEFAULT_TYPE, text: str, morning_key: str) -> None:
    config: Config = context.bot_data["config"]
    await context.bot.send_message(chat_id=config.group_chat_id, text=text)
    logger.info("Опубліковано ранкове повідомлення в General (%s)", morning_key)


async def _clear_preview_messages(
    context: ContextTypes.DEFAULT_TYPE, conn, morning_key: str
) -> None:
    for row in db.preview_messages(conn, morning_key):
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=row["chat_id"], message_id=row["message_id"], reply_markup=None
            )
        except TelegramError as exc:
            logger.error("Не вдалося прибрати кнопки прев'ю chat_id=%s: %s", row["chat_id"], exc)


async def on_preview_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    config: Config = context.bot_data["config"]
    conn = context.bot_data["conn"]
    texts: Texts = context.bot_data["texts"]

    user = update.effective_user
    if user is None or user.id not in config.admin_user_ids:
        await query.answer("Немає доступу", show_alert=True)
        return

    _, morning_key, action = query.data.split(":")
    preview = db.get_preview(conn, morning_key)
    if preview is None:
        await query.answer("Прев'ю більше не активне")
        return
    if preview["status"] != "pending":
        await query.answer("Вже оброблено")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            pass
        return

    if action == "send":
        await query.answer()
        await _publish(context, preview["message_text"], morning_key)
        db.resolve_preview(conn, morning_key, "sent", user.id, datetime.now(UTC).isoformat())
        db.add_send_log(
            conn,
            morning_date=morning_key,
            variant_set=preview["variant_set"],
            variant_id=preview["variant_id"],
            mode="manual",
            sent_by=user.id,
            sent_at=datetime.now(UTC).isoformat(),
            group_message_id=None,
        )
        await _clear_preview_messages(context, conn, morning_key)
        return

    if action == "skip":
        await query.answer("Пропущено")
        db.resolve_preview(conn, morning_key, "skipped", user.id, datetime.now(UTC).isoformat())
        await _clear_preview_messages(context, conn, morning_key)
        return

    if action in ("more", "calm"):
        await query.answer()
        set_name = _CALM_SET if action == "calm" else preview["variant_set"]
        variant = deck.draw_next(conn, set_name, texts.sets[set_name])
        include_arrangements = preview["day_type"] == "workday"
        message_text = build_message(texts, variant, include_arrangements=include_arrangements)
        db.update_preview_content(
            conn,
            morning_key,
            variant_set=set_name,
            variant_id=variant.id,
            message_text=message_text,
        )
        stats_intro = query.message.text.split("\n\n")[0] if query.message.text else ""
        body = f"{stats_intro}\n\nГотовий текст:\n{message_text}"
        try:
            await query.edit_message_text(body, reply_markup=_preview_keyboard(morning_key))
        except TelegramError as exc:
            logger.error("Не вдалося оновити прев'ю: %s", exc)
        return

    logger.warning("Невідома дія прев'ю: %s", query.data)
