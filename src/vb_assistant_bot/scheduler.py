import asyncio
import json
import logging
from dataclasses import dataclass
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
from vb_assistant_bot.handlers import custom as custom_messages
from vb_assistant_bot.message_builder import build_message
from vb_assistant_bot.night_logic import compute_night_stats, is_heavy_night
from vb_assistant_bot.timeutil import to_canonical_utc_iso

_CUSTOM_MESSAGE_POLL_SECONDS = 60

logger = logging.getLogger(__name__)

_DEFAULT_SET_WEEKDAY = "A"
_DEFAULT_SET_WEEKEND = "V"
_CALM_SET = "B"

_PENDING_ACTIONS = frozenset({"send", "more", "calm", "skip"})
_QUEUED_ACTIONS = frozenset({"cancel"})

_EMPTY_KEYBOARD = InlineKeyboardMarkup([])


@dataclass(frozen=True)
class PreviewResult:
    generated: bool  # False лише коли сьогодні вже оброблено (не force) — не помилка
    sent_to: int = 0
    admin_count: int = 0
    triggered: bool = False


def determine_day_type(conn, morning_date: date) -> str:
    manual = db.get_manual_day_type(conn, morning_date.isoformat())
    if manual is not None:
        return manual
    return "weekend" if morning_date.weekday() >= 5 else "workday"


def _pending_keyboard(morning_date: str) -> InlineKeyboardMarkup:
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


def _queued_keyboard(morning_date: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Скасувати", callback_data=f"prev:{morning_date}:cancel")]]
    )


def _pending_body(preview) -> str:
    return f"{preview['stats_intro']}\n\nГотовий текст:\n{preview['message_text']}"


def _queued_body(preview) -> str:
    return (
        f"{preview['stats_intro']}\n\n"
        f"📤 Заплановано до публікації о {_time_label(preview)}:\n{preview['message_text']}"
    )


def _skipped_body(preview) -> str:
    return f"{preview['stats_intro']}\n\n⏭ Пропущено — нічого не буде опубліковано в General."


def _published_body(preview) -> str:
    return f"{preview['stats_intro']}\n\n✅ Опубліковано в General:\n{preview['message_text']}"


def _time_label(preview) -> str:  # noqa: ARG001 — залишає гачок для конфігурованого часу пізніше
    return "8:00"


def _view_for(preview, morning_key: str) -> tuple[str, InlineKeyboardMarkup]:
    status = preview["status"]
    if status == "pending":
        return _pending_body(preview), _pending_keyboard(morning_key)
    if status == "queued":
        return _queued_body(preview), _queued_keyboard(morning_key)
    if status == "skipped":
        return _skipped_body(preview), _EMPTY_KEYBOARD
    if status in ("published", "auto_sent"):
        return _published_body(preview), _EMPTY_KEYBOARD
    return preview["message_text"], _EMPTY_KEYBOARD


def register(application: Application, config: Config, thresholds: Thresholds) -> None:
    tz = ZoneInfo(config.timezone)
    application.job_queue.run_repeating(
        poll_alerts, interval=thresholds.alerts_poll_interval_seconds, first=1
    )
    application.job_queue.run_daily(job_preview, time=thresholds.preview_time.replace(tzinfo=tz))
    application.job_queue.run_daily(
        job_autopublish, time=thresholds.autopublish_time.replace(tzinfo=tz)
    )
    application.job_queue.run_repeating(
        custom_messages.job_dispatch, interval=_CUSTOM_MESSAGE_POLL_SECONDS, first=10
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
            alert_level=record.alert_level,
            threat_types=list(record.threat_types),
            started_at=to_canonical_utc_iso(record.started_at),
            finished_at=to_canonical_utc_iso(record.finished_at) if record.finished_at else None,
            updated_at=to_canonical_utc_iso(record.updated_at),
        )


async def generate_and_send_preview(
    context: ContextTypes.DEFAULT_TYPE, *, force: bool
) -> PreviewResult:
    """Формує й шле прев'ю в особисті всім адмінам — БЕЗУМОВНО (з 2026-09-17,
    після інциденту з мовчазним провалом: масований обстріл не пробив
    пороги тригера, і бот нічого не надіслав). Тригер (`is_heavy_night`)
    лишається рекомендаційним рядком у тексті й умовою для безлюдної
    гілки `job_autopublish`, але не гейтить сам факт надсилання прев'ю.

    force=False (щоденна джоба) — якщо на сьогодні прев'ю вже
    оброблено (не `pending`), не дублює. force=True (`/support`) —
    завжди перегенеровує, ігноруючи попереднє рішення."""
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    texts: Texts = context.bot_data["texts"]
    thresholds: Thresholds = context.bot_data["thresholds"]

    tz = ZoneInfo(config.timezone)
    morning_date = datetime.now(tz).date()
    morning_key = morning_date.isoformat()

    if not force:
        existing = db.get_preview(conn, morning_key)
        if existing is not None and existing["status"] != "pending":
            return PreviewResult(generated=False)

    stats = compute_night_stats(
        conn,
        location_uid=config.alerts_region_uid,
        morning_date=morning_date,
        timezone=config.timezone,
        thresholds=thresholds,
    )
    triggered = is_heavy_night(stats, thresholds)

    day_type = determine_day_type(conn, morning_date)
    default_set = _DEFAULT_SET_WEEKEND if day_type == "weekend" else _DEFAULT_SET_WEEKDAY
    variant = deck.draw_next(conn, default_set, texts.sets[default_set])
    include_arrangements = day_type == "workday"
    message_text = build_message(texts, variant, include_arrangements=include_arrangements)

    db.upsert_preview(
        conn,
        morning_date=morning_key,
        status="pending",
        day_type=day_type,
        variant_set=default_set,
        variant_id=variant.id,
        message_text=message_text,
        stats_json=json.dumps({"count": stats.count}),
        stats_intro=format_stats_summary(stats, triggered),
        triggered=triggered,
        created_at=datetime.now(UTC).isoformat(),
    )

    preview = db.get_preview(conn, morning_key)
    body, keyboard = _view_for(preview, morning_key)
    sent_to = 0
    for admin_id in config.admin_user_ids:
        try:
            sent = await context.bot.send_message(
                chat_id=admin_id, text=body, reply_markup=keyboard
            )
        except TelegramError as exc:
            logger.error("Не вдалося надіслати прев'ю адміну %s: %s", admin_id, exc)
            continue
        db.add_preview_message(conn, morning_key, admin_id, sent.message_id)
        sent_to += 1

    return PreviewResult(
        generated=True,
        sent_to=sent_to,
        admin_count=len(config.admin_user_ids),
        triggered=triggered,
    )


async def job_preview(context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await generate_and_send_preview(context, force=False)
    if result.generated:
        logger.info(
            "Ранкове прев'ю: надіслано %s/%s адмінам, triggered=%s",
            result.sent_to,
            result.admin_count,
            result.triggered,
        )


async def job_autopublish(context: ContextTypes.DEFAULT_TYPE) -> None:
    """08:00. Дві незалежні гілки:

    1. `status == "queued"` — адмін уже натиснув «Надіслати» (у будь-який
       час, незалежно від вердикту алгоритму) — публікуємо те, що він
       обрав, саме зараз, не раніше (ТЗ: дає час скасувати/змінити текст
       до 8:00).
    2. `status == "pending"` і `triggered` — ніхто не відреагував на важку
       ніч: за замовчуванням лише нагадування (перший місяць, ТЗ п.13),
       з `AUTO_PUBLISH_ENABLED=true` — бот публікує сам.

    Спокійна ніч без реакції (`pending`, не `triggered`) — тиша, це
    очікуваний результат, не збій."""
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    tz = ZoneInfo(config.timezone)
    morning_key = datetime.now(tz).date().isoformat()

    preview = db.get_preview(conn, morning_key)
    if preview is None:
        return

    if preview["status"] == "queued":
        await _publish_and_resolve(
            context,
            conn,
            morning_key,
            preview,
            status="published",
            mode="manual",
            sent_by=preview["resolved_by"],
        )
        return

    if preview["status"] != "pending" or not preview["triggered"]:
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

    await _publish_and_resolve(
        context, conn, morning_key, preview, status="auto_sent", mode="auto", sent_by=None
    )


_PUBLISH_RETRY_DELAYS = (3, 8)  # секунди між спробами всередині одного виклику
_PUBLISH_RESCHEDULE_SECONDS = 300  # якщо й повтори не допомогли — ще раз через 5 хв


async def _publish(context: ContextTypes.DEFAULT_TYPE, text: str, morning_key: str) -> None:
    """Публікація в GROUP_CHAT_ID з кількома спробами. Раніше — єдиний
    незахищений виклик, що спрацьовував лише РАЗ НА ДОБУ (job_autopublish
    о 8:00): одна мережева гикавка (httpx.ReadError тощо — PTB обгортає
    в NetworkError/TelegramError) означала, що ранкове повідомлення не
    йшло в General взагалі до наступного дня, а адміни дізнавались лише
    з різкого '⚠️ Помилка в боті' від глобального error-handler."""
    config: Config = context.bot_data["config"]
    attempts = len(_PUBLISH_RETRY_DELAYS) + 1
    for attempt in range(attempts):
        try:
            await context.bot.send_message(chat_id=config.group_chat_id, text=text)
            logger.info("Опубліковано ранкове повідомлення в General (%s)", morning_key)
            return
        except TelegramError as exc:
            if attempt == attempts - 1:
                raise
            delay = _PUBLISH_RETRY_DELAYS[attempt]
            logger.warning(
                "Публікація в General не вдалась (спроба %s/%s, %s): %s — повторюю через %sс",
                attempt + 1,
                attempts,
                morning_key,
                exc,
                delay,
            )
            await asyncio.sleep(delay)


async def _publish_and_resolve(
    context: ContextTypes.DEFAULT_TYPE,
    conn,
    morning_key: str,
    preview,
    *,
    status: str,
    mode: str,
    sent_by: int | None,
) -> None:
    try:
        await _publish(context, preview["message_text"], morning_key)
    except TelegramError as exc:
        config: Config = context.bot_data["config"]
        logger.error(
            "Публікація в General не вдалась після повторів (%s): %s — повтор через %sс",
            morning_key,
            exc,
            _PUBLISH_RESCHEDULE_SECONDS,
        )
        note = (
            f"⚠️ Не вдалося опублікувати ранкове повідомлення в General через мережеву "
            f"помилку ({exc}). Спробую ще раз автоматично за "
            f"{_PUBLISH_RESCHEDULE_SECONDS // 60} хв — нічого робити не треба."
        )
        for admin_id in config.admin_user_ids:
            try:
                await context.bot.send_message(chat_id=admin_id, text=note)
            except TelegramError:
                pass
        context.job_queue.run_once(job_autopublish, when=_PUBLISH_RESCHEDULE_SECONDS)
        return

    now = datetime.now(UTC).isoformat()
    db.resolve_preview(conn, morning_key, status, sent_by, now)
    db.add_send_log(
        conn,
        morning_date=morning_key,
        variant_set=preview["variant_set"],
        variant_id=preview["variant_id"],
        mode=mode,
        sent_by=sent_by,
        sent_at=now,
        group_message_id=None,
    )
    await _broadcast_current_view(context, conn, morning_key)


async def _broadcast_current_view(
    context: ContextTypes.DEFAULT_TYPE, conn, morning_key: str
) -> None:
    """Оновлює вигляд прев'ю в ОСОБИСТИХ УСІХ адмінів під поточний стан
    (ТЗ: 'щоб це бачив кожен адміністратор') — не лише в того, хто щойно
    натиснув кнопку."""
    preview = db.get_preview(conn, morning_key)
    if preview is None:
        return
    body, keyboard = _view_for(preview, morning_key)
    for row in db.preview_messages(conn, morning_key):
        try:
            await context.bot.edit_message_text(
                chat_id=row["chat_id"],
                message_id=row["message_id"],
                text=body,
                reply_markup=keyboard,
            )
        except TelegramError as exc:
            logger.error("Не вдалося оновити прев'ю chat_id=%s: %s", row["chat_id"], exc)


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

    status = preview["status"]
    valid = (status == "pending" and action in _PENDING_ACTIONS) or (
        status == "queued" and action in _QUEUED_ACTIONS
    )
    if not valid:
        await query.answer("Вже оброблено")
        body, keyboard = _view_for(preview, morning_key)
        try:
            await query.edit_message_text(body, reply_markup=keyboard)
        except TelegramError:
            pass
        return

    now = datetime.now(UTC).isoformat()

    if action == "send":
        db.resolve_preview(conn, morning_key, "queued", user.id, now)
        await query.answer("Заплановано до публікації о 8:00")
        await _broadcast_current_view(context, conn, morning_key)
        return

    if action == "cancel":
        db.resolve_preview(conn, morning_key, "pending", None, None)
        await query.answer("Скасовано — оберіть варіант і надішліть знову")
        await _broadcast_current_view(context, conn, morning_key)
        return

    if action == "skip":
        db.resolve_preview(conn, morning_key, "skipped", user.id, now)
        await query.answer("Пропущено")
        await _broadcast_current_view(context, conn, morning_key)
        return

    if action in ("more", "calm"):
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
        await query.answer()
        await _broadcast_current_view(context, conn, morning_key)
        return

    logger.warning("Невідома дія прев'ю: %s", query.data)
