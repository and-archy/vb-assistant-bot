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

VERSION = "2026-10-28"

TEXT = (
    "🆕 Бот оновлено. Що нового:\n\n"
    "🔁 Регулярні — повідомлення, які бот сам публікує в General в обрані дні "
    "о фіксованому часі: текст, фото чи файл з підписом (одним повідомленням). "
    "Наприклад, «Золоті години» пн–пт о 10:00.\n"
    "Можна будь-коли змінити текст, фото, дні чи час, поставити на паузу й "
    "подивитися, як виглядатиме («👁 Як виглядатиме» — надішле лише вам).\n"
    "💼 «Робочі дні» враховують позначки 🌴 / 💼 — у свято, позначене "
    "вихідним, не публікується.\n\n"
    "Нова кнопка — внизу екрана. Повна довідка — «❓ Довідка»."
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
