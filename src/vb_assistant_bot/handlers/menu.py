from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from vb_assistant_bot import input_state
from vb_assistant_bot.access import ensure_admin
from vb_assistant_bot.config import Config
from vb_assistant_bot.handlers import custom, overview, support, texts, weekend

BTN_SUPPORT = "🌅 Прев'ю зараз"
BTN_CUSTOM = "📝 Своє повідомлення"
BTN_SCHEDULED = "🗓 Заплановані"
BTN_STATUS = "📋 Статус"
BTN_WEEKEND = "🌴 Вихідний сьогодні"
BTN_WORKDAY = "💼 Робочий сьогодні"
BTN_HISTORY = "🕘 Історія"
BTN_TEXTS = "✏️ Тексти"
BTN_CANCEL = "❌ Скасувати"
BTN_HELP = "❓ Довідка"

BUTTON_LABELS = (
    BTN_SUPPORT,
    BTN_CUSTOM,
    BTN_SCHEDULED,
    BTN_STATUS,
    BTN_WEEKEND,
    BTN_WORKDAY,
    BTN_HISTORY,
    BTN_TEXTS,
    BTN_CANCEL,
    BTN_HELP,
)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_SUPPORT, BTN_CUSTOM],
        [BTN_SCHEDULED, BTN_STATUS],
        [BTN_WEEKEND, BTN_WORKDAY],
        [BTN_HISTORY, BTN_TEXTS],
        [BTN_CANCEL, BTN_HELP],
    ],
    resize_keyboard=True,
)

HELP_TEXT = (
    "VB Assistant — ранкова підтримка команди після важких ночей.\n\n"
    "Щоранку бот сам оцінює ніч і надсилає сюди прев'ю (статистика + готовий "
    "текст) з кнопками: Надіслати / Інший варіант / Стриманий тон / Пропустити "
    "сьогодні. «Надіслати» питає, коли публікувати: Зараз / фіксований час "
    "(за замовчуванням 7:30) / обрати інший час — до обраного часу можна "
    "скасувати. Усі адміни бачать, хто з них що зробив (нікнейм у дужках).\n\n"
    "Кнопки внизу екрана (не зникають):\n"
    f"{BTN_SUPPORT} — сформувати прев'ю зараз (навіть якщо алгоритм не визнав "
    "ніч важкою).\n"
    f"{BTN_CUSTOM} — написати власне повідомлення й запланувати публікацію в "
    "General на обрані дату й час.\n"
    f"{BTN_SCHEDULED} — усе, що чекає на публікацію, з кнопками Скасувати / "
    "Змінити.\n"
    f"{BTN_STATUS} — стан бота одним екраном: тип дня, прев'ю, автопублікація, "
    "свіжість даних про тривоги, заплановане, помилки.\n"
    f"{BTN_WEEKEND} / {BTN_WORKDAY} — позначити СЬОГОДНІ вихідним/робочим "
    "днем (свято тощо). Для іншої дати — `/markweekend дд.мм.рррр` "
    "(або `/markworkday`).\n"
    f"{BTN_HISTORY} — хто що робив за останні 7 днів.\n"
    f"{BTN_TEXTS} — переглянути, додати, змінити чи вимкнути варіанти текстів "
    "ранкових повідомлень.\n"
    f"{BTN_CANCEL} — скасувати поточний ввід тексту чи часу.\n"
    f"{BTN_HELP} — показати цю довідку ще раз.\n\n"
    "Після публікації під повідомленням з'являються кнопки «✏️ Виправити "
    "текст» і «🗑 Видалити з General» (видалити Telegram дозволяє протягом "
    "48 год).\n\n"
    "Бот сам попередить, якщо: alerts.in.ua довго не відповідає (статистика "
    "може бути неповною), своє повідомлення не вдалося опублікувати, або "
    "сталася помилка — і повідомить, коли все знову гаразд.\n\n"
    "Ті самі дії працюють і командами: /support, /custom, /markweekend, "
    "/markworkday, /cancel, /status, /scheduled, /history, /texts."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return
    await update.effective_message.reply_text(HELP_TEXT, reply_markup=MAIN_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«Скасувати» / /cancel — скидає будь-який незавершений ввід (текст чи
    час свого повідомлення, час публікації прев'ю, виправлення тексту в
    General, текст варіанта)."""
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return
    if input_state.any_active(context.user_data):
        input_state.clear_all(context.user_data)
        await update.effective_message.reply_text("Скасовано.")
    else:
        input_state.clear_all(context.user_data)
        await update.effective_message.reply_text("Нема чого скасовувати.")


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопки завжди виконують свою дію одразу — MessageHandler для кнопок
    зареєстрований ПЕРЕД текстовими хендлерами, тож перехоплює натискання
    першим. Будь-яка кнопка, крім «Скасувати» (сама все чистить із
    відповіддю), скидає незавершений ввід — інакше наступне звичайне
    повідомлення адміна помилково зчиталось би як текст/час."""
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    text = update.effective_message.text if update.effective_message else None

    if text == BTN_CANCEL:
        await cancel(update, context)
        return
    input_state.clear_all(context.user_data)

    if text == BTN_SUPPORT:
        await support.support(update, context)
    elif text == BTN_CUSTOM:
        await custom.start(update, context)
    elif text == BTN_SCHEDULED:
        await overview.scheduled(update, context)
    elif text == BTN_STATUS:
        await overview.status(update, context)
    elif text == BTN_WEEKEND:
        context.args = []
        await weekend.mark_weekend(update, context)
    elif text == BTN_WORKDAY:
        context.args = []
        await weekend.mark_workday(update, context)
    elif text == BTN_HISTORY:
        await overview.history(update, context)
    elif text == BTN_TEXTS:
        await texts.show(update, context)
    elif text == BTN_HELP:
        await start(update, context)
