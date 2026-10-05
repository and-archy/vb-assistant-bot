import logging
from datetime import UTC, datetime

from telegram import Update, User
from telegram.ext import ContextTypes

from vb_assistant_bot import db
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


def display_name(user: User) -> str:
    """Нікнейм (username без @), а якщо його немає — повне ім'я з Telegram."""
    return user.username or user.full_name or str(user.id)


def admin_label(conn, user_id: int | None) -> str | None:
    """Ім'я адміна для рядка «хто виконав дію». None — дію виконав сам бот
    (автопублікація), тоді рядок не показується. Невідомий адмін (ще не
    писав боту після оновлення) — показуємо його id."""
    if user_id is None:
        return None
    return db.get_admin_name(conn, user_id) or f"id {user_id}"


async def remember_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """TypeHandler у групі -1 (виконується ПЕРЕД усіма хендлерами):
    запам'ятовує актуальний нікнейм адміна з кожного його апдейту, щоб
    вигляди прев'ю/своїх повідомлень могли показати, хто що зробив."""
    user = update.effective_user
    config: Config | None = context.bot_data.get("config")
    if user is None or config is None or user.id not in config.admin_user_ids:
        return
    db.upsert_admin_name(
        context.bot_data["conn"], user.id, display_name(user), datetime.now(UTC).isoformat()
    )
