import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from telegram.error import TelegramError
from telegram.ext import ContextTypes

from vb_assistant_bot.config import Config

logger = logging.getLogger(__name__)

# bot_data ключ: {"first": datetime, "last": datetime, "count": int} — є,
# поки після "⚠️ Помилка в боті" адмінам ще не повідомили про відновлення.
_ERROR_STATE_KEY = "error_state"

# Скільки часу без нових помилок вважаємо поверненням до штатного режиму.
# Мережеві збої (httpx.ReadError тощо під час polling) зазвичай ідуть
# серією по кілька штук — без паузи адміни отримували б "відновлено"
# між двома помилками однієї серії.
RECOVERY_QUIET_SECONDS = 180
CHECK_INTERVAL_SECONDS = 60
# Збій зв'язку з Telegram під час отримання оновлень, коротший за це, —
# без повідомлень (щонічний розрив на кілька секунд о 4:10, 2026-10-15):
# PTB сам повторює запит, а «⚠️» під час збою все одно не доходить.
NETWORK_SILENT_SECONDS = 300


def record_error(
    bot_data: dict,
    now: datetime | None = None,
    *,
    network: bool = False,
    error_text: str = "",
) -> None:
    now = now or datetime.now(UTC)
    state = bot_data.get(_ERROR_STATE_KEY)
    if state is None:
        bot_data[_ERROR_STATE_KEY] = {
            "first": now,
            "last": now,
            "count": 1,
            "network_only": network,
            "delivered": False,
            "last_error": error_text,
        }
        return
    state["last"] = now
    state["count"] += 1
    state["network_only"] = state.get("network_only", False) and network
    if error_text:
        state["last_error"] = error_text


def mark_delivered(bot_data: dict) -> None:
    """«⚠️ Помилка в боті» дійшло хоч одному адміну."""
    state = bot_data.get(_ERROR_STATE_KEY)
    if state is not None:
        state["delivered"] = True


def error_state(bot_data: dict) -> dict | None:
    """Поточна серія помилок (для «📋 Статус») або None."""
    return bot_data.get(_ERROR_STATE_KEY)


def _format_local(value: datetime, timezone: str) -> str:
    local = value.astimezone(ZoneInfo(timezone))
    return f"{local.hour}:{local.minute:02d}"


async def job_check_recovery(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Після "⚠️ Помилка в боті" (on_error) адміни не знали, чи бот уже
    оговтався. Ця джоба повідомляє "✅ знову працює", коли помилок не
    було RECOVERY_QUIET_SECONDS і Telegram API відповідає (get_me).
    Стан скидається лише якщо хоч одному адміну вдалося доставити
    повідомлення — інакше спробуємо на наступному запуску."""
    state = context.bot_data.get(_ERROR_STATE_KEY)
    config: Config | None = context.bot_data.get("config")
    if state is None or config is None:
        return
    if (datetime.now(UTC) - state["last"]).total_seconds() < RECOVERY_QUIET_SECONDS:
        return
    try:
        await context.bot.get_me()
    except TelegramError as exc:
        logger.warning("Перевірка відновлення: Telegram API ще недоступний: %s", exc)
        return

    first = _format_local(state["first"], config.timezone)
    last = _format_local(state["last"], config.timezone)
    period = first if first == last else f"{first}–{last}"
    if state.get("network_only"):
        outage = (state["last"] - state["first"]).total_seconds()
        if outage < NETWORK_SILENT_SECONDS:
            context.bot_data.pop(_ERROR_STATE_KEY, None)
            logger.info(
                "Короткий збій зв'язку з Telegram (%s, %s спроб) — без повідомлення",
                period,
                state["count"],
            )
            return
        text = (
            f"⚠️ Не було зв'язку з Telegram з {first} до {last} "
            f"({state['count']} невдалих спроб). Зараз бот працює штатно."
        )
    else:
        text = (
            "✅ Бот знову працює в штатному режимі — помилок немає вже "
            f"{RECOVERY_QUIET_SECONDS // 60} хв.\n"
            f"Було помилок: {state['count']} ({period})."
        )
        if not state.get("delivered") and state.get("last_error"):
            # «⚠️» не дійшло — інакше адмін не дізнається, що саме сталося.
            text += f"\nПомилка: {state['last_error']}"
    delivered = False
    for admin_id in config.admin_user_ids:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
            delivered = True
        except TelegramError as exc:
            logger.error("Не вдалося повідомити адміна %s про відновлення: %s", admin_id, exc)
    if delivered:
        context.bot_data.pop(_ERROR_STATE_KEY, None)
        logger.info("Повідомлено адмінів про відновлення після %s помилок", state["count"])
