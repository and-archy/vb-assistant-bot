import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
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
_QUEUED_PREVIEW_POLL_SECONDS = 60

logger = logging.getLogger(__name__)

_DEFAULT_SET_WEEKDAY = "A"
_DEFAULT_SET_WEEKEND = "V"
_CALM_SET = "B"

_PENDING_ACTIONS = frozenset(
    {"send", "more", "calm", "skip", "send_now", "send_fixed", "send_custom", "send_back"}
)
_QUEUED_ACTIONS = frozenset({"cancel"})

_EMPTY_KEYBOARD = InlineKeyboardMarkup([])

# user_data ключ: якщо є — очікуємо від адміна текст "гг:хх" для
# довільного часу відправки прев'ю з таким morning_date (callback
# "send_custom" в on_preview_action).
_SEND_TIME_STEP_KEY = "prev_send_time_for"

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


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


def _send_time_keyboard(morning_date: str, fixed_label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Зараз", callback_data=f"prev:{morning_date}:send_now")],
            [
                InlineKeyboardButton(
                    f"Відправити {fixed_label}", callback_data=f"prev:{morning_date}:send_fixed"
                )
            ],
            [
                InlineKeyboardButton(
                    "Обрати інший час відправки", callback_data=f"prev:{morning_date}:send_custom"
                )
            ],
            [InlineKeyboardButton("Назад", callback_data=f"prev:{morning_date}:send_back")],
        ]
    )


def _pending_body(preview) -> str:
    return f"{preview['stats_intro']}\n\nГотовий текст:\n{preview['message_text']}"


def _send_time_body(preview) -> str:
    return f"{_pending_body(preview)}\n\nКоли відправити?"


def _queued_body(preview, timezone: str) -> str:
    return (
        f"{preview['stats_intro']}\n\n"
        f"📤 Заплановано до публікації о {_scheduled_label(preview, timezone)}:"
        f"\n{preview['message_text']}"
    )


def _skipped_body(preview) -> str:
    return f"{preview['stats_intro']}\n\n⏭ Пропущено — нічого не буде опубліковано в General."


def _published_body(preview) -> str:
    return f"{preview['stats_intro']}\n\n✅ Опубліковано в General:\n{preview['message_text']}"


def _format_time(value: time) -> str:
    return f"{value.hour}:{value.minute:02d}"


def _scheduled_label(preview, timezone: str) -> str:
    raw = preview["scheduled_at"]
    if not raw:
        return "?"
    local = datetime.fromisoformat(raw).astimezone(ZoneInfo(timezone))
    return _format_time(local.time())


def _parse_hhmm_input(text: str) -> time | None:
    match = _HHMM_RE.match(text.strip())
    if not match:
        return None
    return time(int(match.group(1)), int(match.group(2)))


def _view_for(preview, morning_key: str, timezone: str) -> tuple[str, InlineKeyboardMarkup]:
    status = preview["status"]
    if status == "pending":
        return _pending_body(preview), _pending_keyboard(morning_key)
    if status == "queued":
        return _queued_body(preview, timezone), _queued_keyboard(morning_key)
    if status == "skipped":
        return _skipped_body(preview), _EMPTY_KEYBOARD
    if status in ("published", "auto_sent"):
        return _published_body(preview), _EMPTY_KEYBOARD
    return preview["message_text"], _EMPTY_KEYBOARD


_MISSED_RUN_GRACE_SECONDS = 120


def register(application: Application, config: Config, thresholds: Thresholds) -> None:
    """Інцидент 2026-09-22: `job_preview` (07:01) пропав мовчки —
    APScheduler за замовчуванням дає джобі лише 1с `misfire_grace_time`
    на старт; блокуючий `poll_alerts` (нижче) інколи затримує event loop
    довше — і замість запізнілого запуску APScheduler просто скипає
    його (лише WARNING у логах, без винятку, тож глобальний
    error-handler мовчить). Для щоденних якорів (`job_preview`,
    `job_autopublish`) даємо суттєвий запас — краще виконати на 1-2 хв
    пізніше, ніж не виконати взагалі."""
    tz = ZoneInfo(config.timezone)
    application.job_queue.run_repeating(
        poll_alerts, interval=thresholds.alerts_poll_interval_seconds, first=1
    )
    application.job_queue.run_daily(
        job_preview,
        time=thresholds.preview_time.replace(tzinfo=tz),
        job_kwargs={"misfire_grace_time": _MISSED_RUN_GRACE_SECONDS},
    )
    application.job_queue.run_daily(
        job_autopublish,
        time=thresholds.autopublish_time.replace(tzinfo=tz),
        job_kwargs={"misfire_grace_time": _MISSED_RUN_GRACE_SECONDS},
    )
    application.job_queue.run_repeating(
        custom_messages.job_dispatch, interval=_CUSTOM_MESSAGE_POLL_SECONDS, first=10
    )
    application.job_queue.run_repeating(
        job_publish_queued, interval=_QUEUED_PREVIEW_POLL_SECONDS, first=15
    )


def pop_send_state(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Прибирає стан очікування довільного часу відправки (callback
    "send_custom") — повертає morning_key, якщо такий стан був, інакше
    None. Викликає menu.on_button при перемиканні на іншу кнопку/дію."""
    return context.user_data.pop(_SEND_TIME_STEP_KEY, None)


def reset_send_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    pop_send_state(context)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Текст після callback "send_custom" — очікуваний формат гг:хх для
    сьогоднішньої публікації цього прев'ю. Зареєстрований в окремій
    групі хендлерів (незалежно від custom.on_text), тож не заважає й не
    залежить від потоку /custom."""
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    morning_key = context.user_data.get(_SEND_TIME_STEP_KEY)
    if not morning_key:
        return

    config: Config = context.bot_data["config"]
    user = update.effective_user
    if user is None or user.id not in config.admin_user_ids:
        return

    conn = context.bot_data["conn"]
    preview = db.get_preview(conn, morning_key)
    if preview is None or preview["status"] != "pending":
        pop_send_state(context)
        await update.effective_message.reply_text("Прев'ю більше не активне.")
        return

    text = update.effective_message.text or ""
    parsed = _parse_hhmm_input(text)
    if parsed is None:
        await update.effective_message.reply_text(
            "Невірний формат. Спробуйте ще: гг:хх, наприклад 07:15."
        )
        return

    tz = ZoneInfo(config.timezone)
    now_local = datetime.now(tz)
    target_local = datetime.combine(now_local.date(), parsed, tzinfo=tz)
    if target_local <= now_local:
        await update.effective_message.reply_text(
            "Час уже минув. Вкажіть пізніший сьогоднішній час: гг:хх"
        )
        return

    pop_send_state(context)
    scheduled_at = target_local.astimezone(UTC).isoformat()
    now_utc = datetime.now(UTC).isoformat()
    db.resolve_preview(conn, morning_key, "queued", user.id, now_utc, scheduled_at=scheduled_at)
    await _broadcast_current_view(context, conn, morning_key)
    await update.effective_message.reply_text(f"Заплановано на {_format_time(parsed)}.")


async def poll_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Інцидент 2026-09-22: `fetch_region_history` — синхронний
    `urllib.request` (`alerts_client.py`), і виклик його напряму тут
    блокував ЄДИНИЙ event loop на ~1-2с щоразу (кожні
    `alerts_poll_interval_seconds`) — саме в такому вікні "пропав"
    `job_preview`. `asyncio.to_thread` виносить блокуючий I/O в
    окремий потік, не чіпаючи основний loop."""
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    client: AlertsInUaClient = context.bot_data["alerts_client"]

    try:
        records = await asyncio.to_thread(client.fetch_region_history, config.alerts_region_uid)
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
    body, keyboard = _view_for(preview, morning_key, config.timezone)
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
    """thresholds.autopublish_time (за замовч. 7:30) — гілка на важку ніч
    (`triggered`), яку ніхто не передивився (`status == "pending"`): за
    замовчуванням (`AUTO_PUBLISH_ENABLED=true`, робочий режим з
    2026-09-24) бот публікує сам; з `=false` — лише нагадування адмінам
    (ручний режим тестового періоду, ТЗ п.13).

    `status == "queued"` тут більше НЕ обробляється (2026-09-20) — адмін
    сам обирає час публікації («Зараз» / фіксований / довільний), і саме
    на нього публікує окремий поллер `job_publish_queued`, а не єдиний
    добовий job_autopublish.

    Спокійна ніч без реакції (`pending`, не `triggered`) — тиша, це
    очікуваний результат, не збій."""
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    tz = ZoneInfo(config.timezone)
    morning_key = datetime.now(tz).date().isoformat()

    preview = db.get_preview(conn, morning_key)
    if preview is None:
        return

    if preview["status"] != "pending" or not preview["triggered"]:
        return

    if not config.auto_publish_enabled:
        note = (
            "⏰ Прев'ю на сьогодні досі без реакції. Автопублікація вимкнена "
            "(AUTO_PUBLISH_ENABLED=false — ручний режим). "
            "Натисніть кнопку вище або викличте /support."
        )
        for admin_id in config.admin_user_ids:
            try:
                await context.bot.send_message(chat_id=admin_id, text=note)
            except TelegramError as exc:
                logger.error("Не вдалося надіслати нагадування адміну %s: %s", admin_id, exc)
        return

    await _publish_and_resolve(
        context,
        conn,
        morning_key,
        preview,
        status="auto_sent",
        mode="auto",
        sent_by=None,
        retry_job=job_autopublish,
    )


async def job_publish_queued(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Поллер (як `custom_messages.job_dispatch`): публікує прев'ю зі
    статусом 'queued', чий `scheduled_at` (обраний адміном час — зараз /
    фіксований / довільний) уже настав."""
    conn = context.bot_data["conn"]
    now_iso = datetime.now(UTC).isoformat()
    for preview in db.due_queued_previews(conn, now_iso):
        await _publish_and_resolve(
            context,
            conn,
            preview["morning_date"],
            preview,
            status="published",
            mode="manual",
            sent_by=preview["resolved_by"],
            retry_job=job_publish_queued,
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
    retry_job,
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
        context.job_queue.run_once(retry_job, when=_PUBLISH_RESCHEDULE_SECONDS)
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
    config: Config = context.bot_data["config"]
    preview = db.get_preview(conn, morning_key)
    if preview is None:
        return
    body, keyboard = _view_for(preview, morning_key, config.timezone)
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
        body, keyboard = _view_for(preview, morning_key, config.timezone)
        try:
            await query.edit_message_text(body, reply_markup=keyboard)
        except TelegramError:
            pass
        return

    now = datetime.now(UTC).isoformat()

    if action == "send":
        thresholds: Thresholds = context.bot_data["thresholds"]
        fixed_label = _format_time(thresholds.autopublish_time)
        await query.answer()
        try:
            await query.edit_message_text(
                _send_time_body(preview),
                reply_markup=_send_time_keyboard(morning_key, fixed_label),
            )
        except TelegramError:
            pass
        return

    if action == "send_back":
        await query.answer()
        await _broadcast_current_view(context, conn, morning_key)
        return

    if action == "send_now":
        db.resolve_preview(conn, morning_key, "queued", user.id, now, scheduled_at=now)
        await query.answer("Публікую...")
        await _broadcast_current_view(context, conn, morning_key)
        await job_publish_queued(context)
        return

    if action == "send_fixed":
        thresholds = context.bot_data["thresholds"]
        tz = ZoneInfo(config.timezone)
        now_local = datetime.now(tz)
        target_local = datetime.combine(now_local.date(), thresholds.autopublish_time, tzinfo=tz)
        immediate = target_local <= now_local
        scheduled_at = (now_local if immediate else target_local).astimezone(UTC).isoformat()
        db.resolve_preview(conn, morning_key, "queued", user.id, now, scheduled_at=scheduled_at)
        label = _format_time(thresholds.autopublish_time)
        await query.answer("Публікую..." if immediate else f"Заплановано на {label}")
        await _broadcast_current_view(context, conn, morning_key)
        if immediate:
            await job_publish_queued(context)
        return

    if action == "send_custom":
        context.user_data[_SEND_TIME_STEP_KEY] = morning_key
        await query.answer()
        try:
            await query.edit_message_text(
                "Введіть час публікації сьогодні у форматі гг:хх (наприклад 07:15). "
                "/cancel — скасувати.",
                reply_markup=_EMPTY_KEYBOARD,
            )
        except TelegramError:
            pass
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
