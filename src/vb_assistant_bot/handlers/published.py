"""Дії з уже опублікованим у General повідомленням (ранковим чи своїм):
«✏️ Виправити текст» і «🗑 Видалити з General» (з підтвердженням).
Telegram дозволяє боту видаляти власні повідомлення в групі лише
протягом 48 год — пізніше покажемо помилку від Telegram."""

import logging
from datetime import UTC, datetime

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from vb_assistant_bot import audit, db, input_state, scheduler
from vb_assistant_bot.config import Config
from vb_assistant_bot.handlers import custom
from vb_assistant_bot.keyboards import delete_confirm_keyboard

logger = logging.getLogger(__name__)

_KIND_LABELS = {"prev": "ранкове повідомлення", "custom": "своє повідомлення"}


def _published_target(conn, kind: str, key: str) -> tuple[int, str] | None:
    """(id повідомлення в General, поточний текст) або None, якщо
    повідомлення вже не в стані «опубліковано»."""
    if kind == "prev":
        row = db.get_preview(conn, key)
        if row is None or row["status"] not in ("published", "auto_sent"):
            return None
        mid, text = row["group_message_id"], row["message_text"]
    elif kind == "custom":
        row = db.get_custom_message(conn, int(key))
        if row is None or row["status"] != "sent":
            return None
        mid, text = row["group_message_id"], row["text"]
    else:
        return None
    return (mid, text) if mid is not None else None


async def _refresh(context: ContextTypes.DEFAULT_TYPE, kind: str, key: str) -> None:
    if kind == "prev":
        await scheduler.refresh_preview_views(context, key)
    else:
        await custom.refresh_views(context, int(key))


async def on_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    config: Config = context.bot_data["config"]
    conn = context.bot_data["conn"]
    user = update.effective_user
    if user is None or user.id not in config.admin_user_ids:
        await query.answer("Немає доступу", show_alert=True)
        return

    _, kind, key, action = query.data.split(":")
    target = _published_target(conn, kind, key)
    if target is None:
        await query.answer("Вже неактуально")
        await _refresh(context, kind, key)
        return
    group_message_id, _ = target
    label = _KIND_LABELS[kind]

    if action == "edit":
        input_state.clear_all(context.user_data)
        context.user_data[input_state.PUBLISHED_EDIT] = f"{kind}:{key}"
        await query.answer()
        await context.bot.send_message(
            chat_id=user.id,
            text=f"Надішліть новий текст — ним буде замінено {label} в General. "
            "/cancel — скасувати.",
        )
        return

    if action == "del":
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=delete_confirm_keyboard(kind, key))
        except TelegramError:
            pass
        return

    if action == "del_no":
        await query.answer("Не видаляю")
        await _refresh(context, kind, key)
        return

    if action == "del_yes":
        try:
            await context.bot.delete_message(
                chat_id=config.group_chat_id, message_id=group_message_id
            )
        except TelegramError as exc:
            logger.error("Не вдалося видалити %s з General: %s", label, exc)
            await query.answer(
                f"Не вдалося видалити: {exc}. Telegram дозволяє боту видаляти власні "
                "повідомлення в групі лише протягом 48 год.",
                show_alert=True,
            )
            await _refresh(context, kind, key)
            return
        if kind == "prev":
            db.set_preview_status(conn, key, "deleted")
            db.set_preview_actor(conn, key, user.id, "deleted")
        else:
            db.set_custom_message_status(conn, int(key), "deleted", _now_iso(), updated_by=user.id)
        audit.log(conn, user.id, f"видалив {label} з General")
        await query.answer("Видалено з General")
        await _refresh(context, kind, key)
        return

    logger.warning("Невідома дія з опублікованим повідомленням: %s", query.data)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    pending = context.user_data.get(input_state.PUBLISHED_EDIT)
    if not pending:
        return
    config: Config = context.bot_data["config"]
    user = update.effective_user
    if user is None or user.id not in config.admin_user_ids:
        return

    conn = context.bot_data["conn"]
    context.user_data.pop(input_state.PUBLISHED_EDIT, None)
    kind, key = pending.split(":", 1)
    target = _published_target(conn, kind, key)
    if target is None:
        await update.effective_message.reply_text("Це повідомлення вже не можна виправити.")
        return
    group_message_id, old_text = target
    new_text = update.effective_message.text or ""
    if new_text == old_text:
        await update.effective_message.reply_text("Текст не змінився — нічого не роблю.")
        return
    try:
        await context.bot.edit_message_text(
            chat_id=config.group_chat_id, message_id=group_message_id, text=new_text
        )
    except TelegramError as exc:
        logger.error("Не вдалося виправити текст у General: %s", exc)
        await update.effective_message.reply_text(f"Не вдалося виправити текст у General: {exc}")
        return

    if kind == "prev":
        db.set_preview_published_text(conn, key, new_text)
        db.set_preview_actor(conn, key, user.id, "edited")
    else:
        db.update_custom_message_text(conn, int(key), new_text, _now_iso(), user.id)
    audit.log(conn, user.id, f"виправив текст ({_KIND_LABELS[kind]}) у General")
    await _refresh(context, kind, key)
    await update.effective_message.reply_text("Текст у General виправлено.")
