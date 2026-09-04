import logging
import logging.handlers
import os

from telegram import Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler

from vb_assistant_bot import db, scheduler
from vb_assistant_bot.alerts_client import AlertsInUaClient
from vb_assistant_bot.config import load_config
from vb_assistant_bot.content import load_texts, load_thresholds
from vb_assistant_bot.handlers import menu, support, weekend

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
    application.add_handler(CallbackQueryHandler(scheduler.on_preview_action, pattern=r"^prev:"))

    scheduler.register(application, config, thresholds)

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
