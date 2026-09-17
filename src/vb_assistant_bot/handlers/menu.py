from telegram import Update
from telegram.ext import ContextTypes

from vb_assistant_bot.access import ensure_admin
from vb_assistant_bot.config import Config

_HELP_TEXT = (
    "VB Assistant — ранкова підтримка команди після важких ночей.\n\n"
    "/support — переглянути прев'ю ранкового повідомлення зараз (навіть якщо "
    "алгоритм не визнав ніч важкою).\n"
    "/markweekend [дд.мм.рррр] — позначити дату вихідним днем (свято тощо); "
    "без дати — сьогодні.\n"
    "/markworkday [дд.мм.рррр] — прибрати позначку вихідного, повернути "
    "звичайний робочий режим; без дати — сьогодні.\n"
    "/custom — написати власне повідомлення й запланувати його публікацію "
    "в General на обрані дату й час (бачать і можуть скасувати/змінити "
    "всі адміни). /cancel — скасувати поточний ввід.\n\n"
    "Щоранку бот сам оцінює ніч і надсилає сюди прев'ю (статистика + "
    "готовий текст) з кнопками: Надіслати / Інший варіант / Стриманий "
    "тон / Пропустити сьогодні. «Надіслати» планує публікацію на 8:00 — "
    "до цього часу можна скасувати чи обрати інший варіант."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return
    await update.effective_message.reply_text(_HELP_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)
