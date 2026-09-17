import logging
import logging.handlers
import os

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from vb_assistant_bot import db, scheduler
from vb_assistant_bot.alerts_client import AlertsInUaClient
from vb_assistant_bot.config import Config, load_config
from vb_assistant_bot.content import load_texts, load_thresholds
from vb_assistant_bot.handlers import custom, menu, support, weekend

logger = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_LOG_MAX_BYTES = 5_000_000
_LOG_BACKUP_COUNT = 3


def _configure_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = os.environ.get("LOG_FILE", "").strip()
    if log_file:
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUP_COUNT
            )
        )
    logging.basicConfig(format=_LOG_FORMAT, level=logging.INFO, handlers=handlers)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Без цього необроблена помилка в будь-якому хендлері/джобі просто
    логувалась і зникала — жоден адмін не дізнавався, що бот щось не
    зробив (інцидент 2026-09-17: /support мовчав при збої)."""
    logger.error("Необроблена помилка при обробці %s", update, exc_info=context.error)
    config: Config | None = context.bot_data.get("config")
    if config is None:
        return
    text = f"⚠️ Помилка в боті: {context.error}"
    for admin_id in config.admin_user_ids:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except TelegramError:
            pass


def main() -> None:
    _configure_logging()
    config = load_config()
    texts = load_texts(config.texts_config_path)
    thresholds = load_thresholds(config.thresholds_config_path)
    conn = db.init_db(config.db_path)

    application = ApplicationBuilder().token(config.token).build()
    application.bot_data["conn"] = conn
    application.bot_data["config"] = config
    application.bot_data["texts"] = texts
    application.bot_data["thresholds"] = thresholds
    application.bot_data["alerts_client"] = AlertsInUaClient(config.alerts_api_token)

    application.add_handler(CommandHandler("start", menu.start))
    application.add_handler(CommandHandler("help", menu.help_command))
    application.add_handler(CommandHandler("support", support.support))
    application.add_handler(CommandHandler("markweekend", weekend.mark_weekend))
    application.add_handler(CommandHandler("markworkday", weekend.mark_workday))
    application.add_handler(CommandHandler("custom", custom.start))
    application.add_handler(CommandHandler("cancel", custom.cancel_compose))
    application.add_handler(CallbackQueryHandler(scheduler.on_preview_action, pattern=r"^prev:"))
    application.add_handler(CallbackQueryHandler(custom.on_action, pattern=r"^custom:"))
    # Кнопки — ПЕРЕД загальним текстовим хендлером: у межах однієї групи
    # PTB зупиняється на першому хендлері, чий фільтр збігся, тож
    # натискання кнопки ніколи не потрапляє в custom.on_text.
    application.add_handler(MessageHandler(filters.Text(menu.BUTTON_LABELS), menu.on_button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, custom.on_text))
    application.add_error_handler(on_error)

    scheduler.register(application, config, thresholds)

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
