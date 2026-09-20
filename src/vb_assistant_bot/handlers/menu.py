from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from vb_assistant_bot import scheduler
from vb_assistant_bot.access import ensure_admin
from vb_assistant_bot.config import Config
from vb_assistant_bot.handlers import custom, support, weekend

BTN_SUPPORT = "🌅 Прев'ю зараз"
BTN_CUSTOM = "📝 Своє повідомлення"
BTN_WEEKEND = "🌴 Вихідний сьогодні"
BTN_WORKDAY = "💼 Робочий сьогодні"
BTN_CANCEL = "❌ Скасувати"
BTN_HELP = "❓ Довідка"

BUTTON_LABELS = (BTN_SUPPORT, BTN_CUSTOM, BTN_WEEKEND, BTN_WORKDAY, BTN_CANCEL, BTN_HELP)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_SUPPORT, BTN_CUSTOM], [BTN_WEEKEND, BTN_WORKDAY], [BTN_CANCEL, BTN_HELP]],
    resize_keyboard=True,
)

_HELP_TEXT = (
    "VB Assistant — ранкова підтримка команди після важких ночей.\n\n"
    "Кнопки внизу екрана (не зникають):\n"
    f"{BTN_SUPPORT} — переглянути прев'ю ранкового повідомлення зараз (навіть "
    "якщо алгоритм не визнав ніч важкою).\n"
    f"{BTN_WEEKEND} / {BTN_WORKDAY} — позначити СЬОГОДНІ вихідним/робочим "
    "днем (свято тощо). Для іншої дати — команда `/markweekend дд.мм.рррр` "
    "(або `/markworkday`).\n"
    f"{BTN_CUSTOM} — написати власне повідомлення й запланувати публікацію "
    "в General на обрані дату й час (бачать і можуть скасувати/змінити всі "
    "адміни).\n"
    f"{BTN_CANCEL} — скасувати поточний ввід (текст/час свого повідомлення).\n"
    f"{BTN_HELP} — показати цю довідку ще раз.\n\n"
    "Ті самі дії працюють і командами, хто звик: /support, /markweekend, "
    "/markworkday, /custom, /cancel.\n\n"
    "Щоранку бот сам оцінює ніч і надсилає сюди прев'ю (статистика + "
    "готовий текст) з кнопками: Надіслати / Інший варіант / Стриманий "
    "тон / Пропустити сьогодні. «Надіслати» питає, коли публікувати: "
    "Зараз / фіксований час (за замовчуванням 7:30) / обрати інший час — "
    "до обраного часу можна скасувати чи обрати інший варіант."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return
    await update.effective_message.reply_text(_HELP_TEXT, reply_markup=MAIN_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопки завжди виконують свою дію одразу, навіть якщо в адміна
    незавершений `/custom` (очікує текст чи час) або незавершений вибір
    довільного часу відправки прев'ю (`scheduler` callback "send_custom")
    — MessageHandler для кнопок зареєстрований ПЕРЕД загальним текстовим
    хендлером (custom.on_text), тож перехоплює натискання першим.
    Скидаємо `scheduler`-стан для будь-якої кнопки, крім «Скасувати»
    (сама його чистить із відповіддю), і `custom`-стан для будь-якої,
    крім «Своє повідомлення» (свідомо стартує/перезапускає той самий
    потік) і «Скасувати» — інакше наступне звичайне повідомлення
    адміна помилково зчиталось би як текст/час свого повідомлення."""
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    text = update.effective_message.text if update.effective_message else None

    if text != BTN_CANCEL:
        scheduler.reset_send_state(context)
    if text not in (BTN_CUSTOM, BTN_CANCEL):
        custom.reset_state(context)

    if text == BTN_SUPPORT:
        await support.support(update, context)
    elif text == BTN_CUSTOM:
        await custom.start(update, context)
    elif text == BTN_WEEKEND:
        context.args = []
        await weekend.mark_weekend(update, context)
    elif text == BTN_WORKDAY:
        context.args = []
        await weekend.mark_workday(update, context)
    elif text == BTN_CANCEL:
        if scheduler.pop_send_state(context) is not None:
            await update.effective_message.reply_text("Скасовано.")
        else:
            await custom.cancel_compose(update, context)
    elif text == BTN_HELP:
        await start(update, context)
