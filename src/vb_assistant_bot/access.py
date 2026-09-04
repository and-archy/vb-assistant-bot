import logging

from telegram import Update

from vb_assistant_bot.config import Config

logger = logging.getLogger(__name__)


async def ensure_admin(update: Update, config: Config) -> bool:
    """Гейт для команд у особистих (ТЗ п.4 — список адмінів у конфізі,
    мінімум двоє). Груповий чат General не гейтиться — бот там лише
    публікує, не обробляє вхідні повідомлення учасників."""
    user = update.effective_user
    if user is not None and user.id in config.admin_user_ids:
        return True

    logger.warning("Відхилено доступ для user_id=%s", user.id if user else None)
    if update.effective_message is not None:
        await update.effective_message.reply_text("Немає доступу.")
    return False
