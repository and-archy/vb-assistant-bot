"""Одноразове повідомлення «Що нового» адмінам після оновлення бота.
Надсилається разом із новою клавіатурою — без цього нові кнопки меню в
адмінів не з'являться (Telegram показує стару клавіатуру, доки бот не
надішле нову). Кожному адміну — один раз на версію (bot_state)."""

import logging

from telegram.error import TelegramError
from telegram.ext import ContextTypes

from vb_assistant_bot import db
from vb_assistant_bot.config import Config
from vb_assistant_bot.handlers.menu import MAIN_KEYBOARD

logger = logging.getLogger(__name__)

VERSION = "2026-10-05"

TEXT = (
    "🆕 Бот оновлено. Що нового:\n\n"
    "👤 У прев'ю, своїх повідомленнях і позначках вихідних/робочих днів видно, "
    "хто з адмінів виконав дію (нікнейм у дужках).\n"
    "📋 Статус — стан бота одним екраном.\n"
    "🗓 Заплановані — усе, що чекає на публікацію, з кнопками керування.\n"
    "🕘 Історія — хто що робив за останні 7 днів.\n"
    "✏️ Тексти — додавати, змінювати й вимикати варіанти ранкових повідомлень "
    "прямо в боті.\n"
    "✏️/🗑 Під опублікованим повідомленням — «Виправити текст» і «Видалити з "
    "General» (видалити можна протягом 48 год).\n"
    "⚠️ Бот попередить, якщо alerts.in.ua довго не відповідає або своє "
    "повідомлення не вдалося опублікувати, а після «⚠️ Помилка в боті» — "
    "повідомить, коли знову працює штатно.\n\n"
    "Нові кнопки — внизу екрана. Повна довідка — «❓ Довідка»."
)


def _key(admin_id: int) -> str:
    return f"whats_new_seen:{admin_id}"


async def job_announce(context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    for admin_id in config.admin_user_ids:
        if db.get_state(conn, _key(admin_id)) == VERSION:
            continue
        try:
            await context.bot.send_message(chat_id=admin_id, text=TEXT, reply_markup=MAIN_KEYBOARD)
        except TelegramError as exc:
            # Адмін ще не писав боту /start — спробуємо при наступному запуску.
            logger.warning("Не вдалося надіслати «Що нового» адміну %s: %s", admin_id, exc)
            continue
        db.set_state(conn, _key(admin_id), VERSION)
