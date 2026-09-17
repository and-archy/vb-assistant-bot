import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from vb_assistant_bot import db
from vb_assistant_bot.access import ensure_admin
from vb_assistant_bot.config import Config

logger = logging.getLogger(__name__)

_STEP_KEY = "custom_step"
_TEXT_KEY = "custom_text"
_EDIT_ID_KEY = "custom_edit_id"

_DATETIME_FORMAT = "%d.%m.%Y %H:%M"
_EMPTY_KEYBOARD = InlineKeyboardMarkup([])


def _keyboard(custom_message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Скасувати", callback_data=f"custom:{custom_message_id}:cancel")],
            [
                InlineKeyboardButton(
                    "Змінити текст", callback_data=f"custom:{custom_message_id}:edit_text"
                ),
                InlineKeyboardButton(
                    "Змінити час", callback_data=f"custom:{custom_message_id}:edit_time"
                ),
            ],
        ]
    )


def _local_label(scheduled_at_utc_iso: str, timezone: str) -> str:
    dt = datetime.fromisoformat(scheduled_at_utc_iso).astimezone(ZoneInfo(timezone))
    return dt.strftime(_DATETIME_FORMAT)


def _view(row, timezone: str) -> tuple[str, InlineKeyboardMarkup]:
    when = _local_label(row["scheduled_at"], timezone)
    if row["status"] == "scheduled":
        body = f"📝 Заплановане повідомлення на {when}:\n\n{row['text']}"
        return body, _keyboard(row["id"])
    if row["status"] == "cancelled":
        return f"📝 Скасовано (мало піти о {when}):\n\n{row['text']}", _EMPTY_KEYBOARD
    return f"✅ Опубліковано о {when}:\n\n{row['text']}", _EMPTY_KEYBOARD


def _parse_datetime(text: str, timezone: str) -> datetime | None:
    try:
        naive = datetime.strptime(text.strip(), _DATETIME_FORMAT)
    except ValueError:
        return None
    return naive.replace(tzinfo=ZoneInfo(timezone))


async def _broadcast(context: ContextTypes.DEFAULT_TYPE, custom_message_id: int) -> None:
    """Шле/оновлює вигляд у ВСІХ адмінів (ТЗ: 'відкладені повідомлення
    теж побачить і інший адміністратор')."""
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    row = db.get_custom_message(conn, custom_message_id)
    if row is None:
        return
    body, keyboard = _view(row, config.timezone)
    existing = {
        r["chat_id"]: r["message_id"] for r in db.custom_message_previews(conn, custom_message_id)
    }
    for admin_id in config.admin_user_ids:
        if admin_id in existing:
            try:
                await context.bot.edit_message_text(
                    chat_id=admin_id,
                    message_id=existing[admin_id],
                    text=body,
                    reply_markup=keyboard,
                )
            except TelegramError as exc:
                logger.error("Не вдалося оновити своє повідомлення chat_id=%s: %s", admin_id, exc)
            continue
        try:
            sent = await context.bot.send_message(
                chat_id=admin_id, text=body, reply_markup=keyboard
            )
        except TelegramError as exc:
            logger.error("Не вдалося надіслати своє повідомлення адміну %s: %s", admin_id, exc)
            continue
        db.add_custom_message_preview(conn, custom_message_id, admin_id, sent.message_id)


def _clear_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(_STEP_KEY, None)
    context.user_data.pop(_TEXT_KEY, None)
    context.user_data.pop(_EDIT_ID_KEY, None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return
    context.user_data[_STEP_KEY] = "await_text"
    await update.effective_message.reply_text(
        "Введіть текст власного повідомлення. /cancel — скасувати."
    )


async def cancel_compose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return
    if context.user_data.get(_STEP_KEY):
        _clear_state(context)
        await update.effective_message.reply_text("Скасовано.")
    else:
        await update.effective_message.reply_text("Нема чого скасовувати.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    step = context.user_data.get(_STEP_KEY)
    if not step:
        return

    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return

    conn = context.bot_data["conn"]
    text = update.effective_message.text
    now_utc = datetime.now(UTC).isoformat()

    if step == "await_text":
        context.user_data[_TEXT_KEY] = text
        context.user_data[_STEP_KEY] = "await_time"
        await update.effective_message.reply_text(
            f"Коли надіслати? Формат: дд.мм.рррр гг:хх (за {config.timezone}), "
            "наприклад 25.12.2026 09:00."
        )
        return

    if step == "await_time":
        parsed = _parse_datetime(text, config.timezone)
        if parsed is None or parsed <= datetime.now(ZoneInfo(config.timezone)):
            await update.effective_message.reply_text(
                "Невірний формат або час уже минув. Спробуйте ще: дд.мм.рррр гг:хх"
            )
            return
        message_text = context.user_data[_TEXT_KEY]
        _clear_state(context)
        custom_message_id = db.create_custom_message(
            conn,
            text=message_text,
            scheduled_at=parsed.astimezone(UTC).isoformat(),
            created_by=update.effective_user.id,
            created_at=now_utc,
        )
        await _broadcast(context, custom_message_id)
        await update.effective_message.reply_text(
            f"Заплановано на {parsed.strftime(_DATETIME_FORMAT)}."
        )
        return

    if step == "await_edit_text":
        custom_message_id = context.user_data[_EDIT_ID_KEY]
        _clear_state(context)
        db.update_custom_message_text(conn, custom_message_id, text, now_utc)
        await _broadcast(context, custom_message_id)
        await update.effective_message.reply_text("Текст оновлено.")
        return

    if step == "await_edit_time":
        parsed = _parse_datetime(text, config.timezone)
        if parsed is None or parsed <= datetime.now(ZoneInfo(config.timezone)):
            await update.effective_message.reply_text(
                "Невірний формат або час уже минув. Спробуйте ще: дд.мм.рррр гг:хх"
            )
            return
        custom_message_id = context.user_data[_EDIT_ID_KEY]
        _clear_state(context)
        db.update_custom_message_time(
            conn, custom_message_id, parsed.astimezone(UTC).isoformat(), now_utc
        )
        await _broadcast(context, custom_message_id)
        await update.effective_message.reply_text(
            f"Час оновлено на {parsed.strftime(_DATETIME_FORMAT)}."
        )
        return


async def on_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    config: Config = context.bot_data["config"]
    conn = context.bot_data["conn"]

    user = update.effective_user
    if user is None or user.id not in config.admin_user_ids:
        await query.answer("Немає доступу", show_alert=True)
        return

    _, raw_id, action = query.data.split(":")
    custom_message_id = int(raw_id)
    row = db.get_custom_message(conn, custom_message_id)
    if row is None or row["status"] != "scheduled":
        await query.answer("Вже оброблено")
        if row is not None:
            body, keyboard = _view(row, config.timezone)
            try:
                await query.edit_message_text(body, reply_markup=keyboard)
            except TelegramError:
                pass
        return

    if action == "cancel":
        db.set_custom_message_status(
            conn, custom_message_id, "cancelled", datetime.now(UTC).isoformat()
        )
        await query.answer("Скасовано")
        await _broadcast(context, custom_message_id)
        return

    if action == "edit_text":
        context.user_data[_STEP_KEY] = "await_edit_text"
        context.user_data[_EDIT_ID_KEY] = custom_message_id
        await query.answer()
        await context.bot.send_message(chat_id=user.id, text="Введіть новий текст повідомлення:")
        return

    if action == "edit_time":
        context.user_data[_STEP_KEY] = "await_edit_time"
        context.user_data[_EDIT_ID_KEY] = custom_message_id
        await query.answer()
        await context.bot.send_message(
            chat_id=user.id, text="Введіть нову дату й час: дд.мм.рррр гг:хх"
        )
        return

    logger.warning("Невідома дія свого повідомлення: %s", query.data)


async def job_dispatch(context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    now_iso = datetime.now(UTC).isoformat()
    for row in db.due_custom_messages(conn, now_iso):
        try:
            await context.bot.send_message(chat_id=config.group_chat_id, text=row["text"])
        except TelegramError as exc:
            logger.error("Не вдалося опублікувати своє повідомлення id=%s: %s", row["id"], exc)
            continue
        db.mark_custom_message_sent(conn, row["id"], now_iso)
        await _broadcast(context, row["id"])
