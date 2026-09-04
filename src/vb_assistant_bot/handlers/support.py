from telegram import Update
from telegram.ext import ContextTypes

from vb_assistant_bot import scheduler
from vb_assistant_bot.access import ensure_admin
from vb_assistant_bot.config import Config


async def support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return
    await scheduler.generate_and_send_preview(context, force=True)
