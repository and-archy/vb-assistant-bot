import logging
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from vb_assistant_bot import audit, db
from vb_assistant_bot.access import display_name, ensure_admin
from vb_assistant_bot.config import Config

logger = logging.getLogger(__name__)


def _parse_date_arg(args: list[str], timezone: str) -> date | None:
    if not args:
        return datetime.now(ZoneInfo(timezone)).date()
    try:
        return datetime.strptime(args[0], "%d.%m.%Y").date()
    except ValueError:
        return None


async def _set_day_type(update: Update, context: ContextTypes.DEFAULT_TYPE, day_type: str) -> None:
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return

    target = _parse_date_arg(context.args, config.timezone)
    if target is None:
        await update.effective_message.reply_text(
            "Формат дати: дд.мм.рррр (наприклад 25.12.2026), або без аргументу — сьогодні."
        )
        return

    conn = context.bot_data["conn"]
    user = update.effective_user
    db.set_manual_day_type(
        conn, target.isoformat(), day_type, user.id, datetime.now(UTC).isoformat()
    )
    label = "вихідний" if day_type == "weekend" else "робочий"
    text = f"📅 {target.strftime('%d.%m.%Y')} позначено як {label} день ({display_name(user)})."
    audit.log(conn, user.id, f"позначив {target.strftime('%d.%m.%Y')} як {label} день")
    await update.effective_message.reply_text(text)
    # Решті адмінів — щоб бачили, хто змінив тип дня (впливає на набір
    # текстів ранкового прев'ю).
    for admin_id in config.admin_user_ids:
        if admin_id == user.id:
            continue
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except TelegramError as exc:
            logger.error("Не вдалося сповістити адміна %s про зміну типу дня: %s", admin_id, exc)


async def mark_weekend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_day_type(update, context, "weekend")


async def mark_workday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_day_type(update, context, "workday")
