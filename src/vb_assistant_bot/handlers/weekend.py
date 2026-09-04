from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

from vb_assistant_bot import db
from vb_assistant_bot.access import ensure_admin
from vb_assistant_bot.config import Config


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
    db.set_manual_day_type(
        conn, target.isoformat(), day_type, update.effective_user.id, datetime.now(UTC).isoformat()
    )
    label = "вихідний" if day_type == "weekend" else "робочий"
    await update.effective_message.reply_text(
        f"{target.strftime('%d.%m.%Y')} позначено як {label} день."
    )


async def mark_weekend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_day_type(update, context, "weekend")


async def mark_workday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_day_type(update, context, "workday")
