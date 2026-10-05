"""Кнопка «✏️ Тексти»: перегляд і редагування варіантів ранкових
повідомлень без правки config/texts.json на сервері (див. text_store)."""

import logging
from dataclasses import replace

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from vb_assistant_bot import audit, db, input_state, text_store
from vb_assistant_bot.access import ensure_admin
from vb_assistant_bot.config import Config
from vb_assistant_bot.content import Texts

logger = logging.getLogger(__name__)

_PREVIEW_CHARS = 60
_IDS_PER_ROW = 4


def _file_texts(context: ContextTypes.DEFAULT_TYPE) -> Texts:
    return context.bot_data.get("file_texts") or context.bot_data["texts"]


def _reload(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Колода й прев'ю беруть тексти з bot_data["texts"] — оновлюємо одразу."""
    context.bot_data["texts"] = text_store.effective_texts(
        _file_texts(context), context.bot_data["conn"]
    )


def _short(text: str) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= _PREVIEW_CHARS else one_line[:_PREVIEW_CHARS] + "…"


def _sets_view(context) -> tuple[str, InlineKeyboardMarkup]:
    by_set = text_store.variants_by_set(_file_texts(context), context.bot_data["conn"])
    lines = ["✏️ Тексти ранкових повідомлень. Оберіть набір:", ""]
    buttons = []
    for set_name, items in by_set.items():
        enabled = sum(1 for i in items if i.enabled)
        label = f"{set_name} — {text_store.set_label(set_name)}"
        lines.append(f"{label}: {enabled} з {len(items)} увімкнено")
        buttons.append([InlineKeyboardButton(label, callback_data=f"txt:set:{set_name}")])
    lines += [
        "",
        "Блоки «домовленості» й «контакти» змінюються лише у файлі config/texts.json.",
    ]
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


def _set_view(context, set_name: str) -> tuple[str, InlineKeyboardMarkup]:
    items = text_store.variants_by_set(_file_texts(context), context.bot_data["conn"])[set_name]
    lines = [f"Набір {set_name} — {text_store.set_label(set_name)}:", ""]
    for item in items:
        mark = "✅" if item.enabled else "🚫"
        extra = " (додано в боті)" if item.from_bot else (" (змінено)" if item.edited else "")
        lines.append(f"{mark} {item.id}{extra}: {_short(item.text)}")
    lines += ["", "Натисніть номер варіанта, щоб переглянути чи змінити його."]
    ids = [InlineKeyboardButton(i.id, callback_data=f"txt:var:{i.id}") for i in items]
    rows = [ids[n : n + _IDS_PER_ROW] for n in range(0, len(ids), _IDS_PER_ROW)]
    rows.append([InlineKeyboardButton("➕ Додати варіант", callback_data=f"txt:add:{set_name}")])
    rows.append([InlineKeyboardButton("⬅️ До наборів", callback_data="txt:sets")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _variant_view(info: text_store.VariantInfo) -> tuple[str, InlineKeyboardMarkup]:
    state = "увімкнено" if info.enabled else "вимкнено (не потрапляє в ранкові повідомлення)"
    origin = "додано в боті" if info.from_bot else ("змінено в боті" if info.edited else "з файлу")
    body = f"Варіант {info.id} — {state}, {origin}:\n\n{info.text}"
    toggle = "🚫 Вимкнути" if info.enabled else "✅ Увімкнути"
    rows = [
        [
            InlineKeyboardButton(toggle, callback_data=f"txt:toggle:{info.id}"),
            InlineKeyboardButton("✏️ Змінити текст", callback_data=f"txt:edit:{info.id}"),
        ]
    ]
    if info.from_bot:
        rows.append([InlineKeyboardButton("🗑 Видалити", callback_data=f"txt:del:{info.id}")])
    elif info.edited:
        rows.append(
            [
                InlineKeyboardButton(
                    "↩️ Повернути текст з файлу", callback_data=f"txt:reset:{info.id}"
                )
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ До набору", callback_data=f"txt:set:{info.set_name}")])
    return body, InlineKeyboardMarkup(rows)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return
    body, keyboard = _sets_view(context)
    await update.effective_message.reply_text(body, reply_markup=keyboard)


async def _edit(query, body: str, keyboard: InlineKeyboardMarkup) -> None:
    try:
        await query.edit_message_text(body, reply_markup=keyboard)
    except TelegramError as exc:
        logger.warning("Не вдалося оновити екран текстів: %s", exc)


def _is_last_enabled(context, info: text_store.VariantInfo) -> bool:
    items = text_store.variants_by_set(_file_texts(context), context.bot_data["conn"])
    return info.enabled and sum(1 for i in items[info.set_name] if i.enabled) == 1


async def on_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    config: Config = context.bot_data["config"]
    conn = context.bot_data["conn"]
    user = update.effective_user
    if user is None or user.id not in config.admin_user_ids:
        await query.answer("Немає доступу", show_alert=True)
        return

    parts = query.data.split(":")
    action = parts[1]
    arg = parts[2] if len(parts) > 2 else ""
    file_texts = _file_texts(context)

    if action == "sets":
        await query.answer()
        await _edit(query, *_sets_view(context))
        return
    if action == "set":
        if arg not in file_texts.sets:
            await query.answer("Немає такого набору")
            return
        await query.answer()
        await _edit(query, *_set_view(context, arg))
        return
    if action == "add":
        if arg not in file_texts.sets:
            await query.answer("Немає такого набору")
            return
        input_state.clear_all(context.user_data)
        context.user_data[input_state.TEXTS_STEP] = f"add:{arg}"
        await query.answer()
        await context.bot.send_message(
            chat_id=user.id,
            text=f"Надішліть текст нового варіанта для набору {arg} "
            f"({text_store.set_label(arg)}). Лише варіативну частину — домовленості "
            "й контакти бот додасть сам. /cancel — скасувати.",
        )
        return

    info = text_store.find(file_texts, conn, arg)
    if info is None:
        await query.answer("Варіант не знайдено")
        await _edit(query, *_sets_view(context))
        return

    if action == "var":
        await query.answer()
        await _edit(query, *_variant_view(info))
        return
    if action == "edit":
        input_state.clear_all(context.user_data)
        context.user_data[input_state.TEXTS_STEP] = f"edit:{info.id}"
        await query.answer()
        await context.bot.send_message(
            chat_id=user.id,
            text=f"Надішліть новий текст для варіанта {info.id}. /cancel — скасувати.",
        )
        return
    if action == "toggle":
        if _is_last_enabled(context, info):
            await query.answer(
                "Це останній увімкнений варіант набору — спершу увімкніть або додайте інший.",
                show_alert=True,
            )
            return
        updated = replace(info, enabled=not info.enabled)
        text_store.save(
            conn, updated, file_text=text_store.file_text(file_texts, info.id), user_id=user.id
        )
        _reload(context)
        audit.log(
            conn, user.id, f"{'увімкнув' if updated.enabled else 'вимкнув'} варіант {info.id}"
        )
        await query.answer("Увімкнено" if updated.enabled else "Вимкнено")
        await _edit(query, *_variant_view(updated))
        return
    if action == "reset" and not info.from_bot:
        original = text_store.file_text(file_texts, info.id)
        updated = replace(info, text=original, edited=False)
        text_store.save(conn, updated, file_text=original, user_id=user.id)
        _reload(context)
        audit.log(conn, user.id, f"повернув текст варіанта {info.id} з файлу")
        await query.answer("Повернуто текст з файлу")
        await _edit(query, *_variant_view(updated))
        return
    if action == "del" and info.from_bot:
        if _is_last_enabled(context, info):
            await query.answer(
                "Це останній увімкнений варіант набору — спершу увімкніть або додайте інший.",
                show_alert=True,
            )
            return
        db.delete_text_variant(conn, info.id)
        _reload(context)
        audit.log(conn, user.id, f"видалив варіант {info.id}")
        await query.answer("Видалено")
        await _edit(query, *_set_view(context, info.set_name))
        return

    logger.warning("Невідома дія з текстами: %s", query.data)
    await query.answer()


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    pending = context.user_data.get(input_state.TEXTS_STEP)
    if not pending:
        return
    config: Config = context.bot_data["config"]
    user = update.effective_user
    if user is None or user.id not in config.admin_user_ids:
        return

    conn = context.bot_data["conn"]
    file_texts = _file_texts(context)
    text = (update.effective_message.text or "").strip()
    if not text:
        await update.effective_message.reply_text("Порожній текст. Надішліть текст або /cancel.")
        return
    context.user_data.pop(input_state.TEXTS_STEP, None)
    step, arg = pending.split(":", 1)

    if step == "add":
        variant_id = text_store.next_variant_id(file_texts, conn, arg)
        info = text_store.VariantInfo(variant_id, arg, text, True, True, False)
        text_store.save(conn, info, file_text=None, user_id=user.id)
        _reload(context)
        audit.log(conn, user.id, f"додав варіант {variant_id}")
        body, keyboard = _variant_view(info)
        await update.effective_message.reply_text(
            f"Додано варіант {variant_id}.\n\n{body}", reply_markup=keyboard
        )
        return

    info = text_store.find(file_texts, conn, arg)
    if info is None:
        await update.effective_message.reply_text("Варіант не знайдено — можливо, його видалили.")
        return
    original = text_store.file_text(file_texts, info.id)
    updated = replace(info, text=text, edited=original is not None and text != original)
    text_store.save(conn, updated, file_text=original, user_id=user.id)
    _reload(context)
    audit.log(conn, user.id, f"змінив текст варіанта {info.id}")
    body, keyboard = _variant_view(updated)
    await update.effective_message.reply_text(f"Текст оновлено.\n\n{body}", reply_markup=keyboard)
