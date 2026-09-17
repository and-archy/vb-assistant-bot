import logging

from telegram import Update
from telegram.ext import ContextTypes

from vb_assistant_bot import scheduler
from vb_assistant_bot.access import ensure_admin
from vb_assistant_bot.config import Config

logger = logging.getLogger(__name__)


async def support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/support має завжди щось відповісти в чат, де його викликали —
    інцидент 2026-09-17: команда мовчала при збої, і адмін не міг
    зрозуміти, чи бот взагалі отримав команду."""
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return

    try:
        result = await scheduler.generate_and_send_preview(context, force=True)
    except Exception:
        logger.exception("/support: не вдалося сформувати прев'ю")
        await update.effective_message.reply_text(
            "Не вдалося сформувати прев'ю через помилку в боті. "
            "Дивіться логи (journalctl -u vb-assistant-bot)."
        )
        return

    if result.sent_to == 0:
        await update.effective_message.reply_text(
            "Прев'ю сформовано, але жодному адміну не вдалося надіслати в особисті "
            "(перевірте, чи всі писали боту /start)."
        )
        return

    await update.effective_message.reply_text(
        f"Прев'ю надіслано в особисті {result.sent_to}/{result.admin_count} адмінам."
    )
