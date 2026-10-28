"""Регулярні повідомлення (2026-10-28) — кнопка «🔁 Регулярні» /
`/recurring`: той самий текст, фото з підписом або файл з підписом
публікується в General в обрані дні тижня о фіксованому часі (перше —
«Золоті години» пн–пт о 10:00, яке раніше вручну слала офіс-менеджерка).

Ввід — той самий легкий стейт у `user_data`, що й у `/custom`
(`input_state.RECURRING_STEP` + чернетка `RECURRING_DRAFT`):
вміст (текст / фото / файл) → підпис, якщо фото без підпису → дні
(інлайн-кнопки) → час. Зміни вже створеного — тими самими кроками по
одному. Публікує поллер `job_dispatch` раз на хвилину."""

import logging
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from vb_assistant_bot import audit, db, input_state, scheduler
from vb_assistant_bot.access import admin_label, ensure_admin
from vb_assistant_bot.config import Config

logger = logging.getLogger(__name__)

POLL_SECONDS = 60
# Бот лежав або не міг достукатись до Telegram у сам час публікації —
# наздоганяємо не пізніше ніж за стільки після нього (о 14:00 «Золоті
# години 10:00–15:00» вже ні до чого).
CATCH_UP = timedelta(minutes=60)

WORKDAYS = "workdays"  # дні — за типом дня бота (з урахуванням 🌴/💼)
_MON_FRI = "01234"
_EVERY_DAY = "0123456"
_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "нд")

# Обмеження Telegram: підпис до фото/файлу — 1024 символи, текст — 4096.
_CAPTION_LIMIT = 1024
_TEXT_LIMIT = 4096
_NO_CAPTION = "-"

_STEP_KEY = input_state.RECURRING_STEP
_DRAFT_KEY = input_state.RECURRING_DRAFT

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_EMPTY_KEYBOARD = InlineKeyboardMarkup([])


# --- розклад ------------------------------------------------------------------


def _parse_hhmm(text: str) -> time | None:
    match = _HHMM_RE.match(text.strip())
    if not match:
        return None
    return time(int(match.group(1)), int(match.group(2)))


def _post_time(post) -> time:
    hour, minute = post["post_time"].split(":")
    return time(int(hour), int(minute))


def days_label(days: str) -> str:
    if days == WORKDAYS:
        return "у робочі дні"
    if days == _EVERY_DAY:
        return "щодня"
    if days == _MON_FRI:
        return "пн–пт"
    return ", ".join(_WEEKDAYS[int(d)] for d in sorted(days))


def schedule_label(post) -> str:
    return f"{days_label(post['days'])} о {post['post_time']}"


def _day_matches(days: str, day: date, day_type: str) -> bool:
    if days == WORKDAYS:
        return day_type == "workday"
    return str(day.weekday()) in days


def is_due(post, now_local: datetime, day_type: str) -> bool:
    """Чи публікувати зараз: сьогоднішній день підходить, час настав (але
    не пізніше CATCH_UP), сьогодні ще не публікували, і час не вже минув
    на момент, коли розклад задали (створили о 10:20 на 10:00 — перша
    публікація завтра, а не одразу)."""
    today = now_local.date()
    if post["status"] != "active" or post["last_sent_on"] == today.isoformat():
        return False
    if not _day_matches(post["days"], today, day_type):
        return False
    slot = datetime.combine(today, _post_time(post), tzinfo=now_local.tzinfo)
    if not slot <= now_local < slot + CATCH_UP:
        return False
    return slot >= datetime.fromisoformat(post["schedule_set_at"])


def next_run(conn, post, now_local: datetime) -> datetime | None:
    """Найближча майбутня публікація (у межах двох тижнів) — для вигляду
    й підтвердження. Тип дня для «робочих днів» — з урахуванням ручних
    позначок 🌴/💼, уже відомих на цей момент."""
    if post["status"] != "active":
        return None
    post_time = _post_time(post)
    for offset in range(14):
        day = now_local.date() + timedelta(days=offset)
        if offset == 0 and post["last_sent_on"] == day.isoformat():
            continue
        if not _day_matches(post["days"], day, scheduler.determine_day_type(conn, day)):
            continue
        slot = datetime.combine(day, post_time, tzinfo=now_local.tzinfo)
        if slot > now_local:
            return slot
    return None


def _when_label(value: datetime) -> str:
    return f"{_WEEKDAYS[value.weekday()]} {value:%d.%m} о {value:%H:%M}"


# --- вигляд для адмінів -----------------------------------------------------------


def _short(post) -> str:
    """Коротка назва для журналу й сповіщень: перший рядок тексту."""
    first_line = (post["text"] or "").strip().splitlines()
    if first_line:
        line = first_line[0]
        return f"«{line[:40]}…»" if len(line) > 40 else f"«{line}»"
    return {"photo": "фото без підпису", "document": "файл без підпису"}.get(
        post["media_type"], "без тексту"
    )


def _media_line(post) -> str:
    if post["media_type"] == "photo":
        return "🖼 З фото."
    if post["media_type"] == "document":
        name = f" «{post['file_name']}»" if post["file_name"] else ""
        return f"📎 З файлом{name}."
    return ""


def _authors(conn, post) -> str:
    parts = [admin_label(conn, post["created_by"])]
    editor = admin_label(conn, post["updated_by"])
    if editor:
        verb = {"deleted": "видалив", "paused": "призупинив"}.get(post["status"], "змінив")
        parts.append(f"{verb} {editor}")
    return f" ({', '.join(p for p in parts if p)})"


def _keyboard(post) -> InlineKeyboardMarkup:
    pid = post["id"]
    toggle = (
        InlineKeyboardButton("⏸ Пауза", callback_data=f"rec:{pid}:pause")
        if post["status"] == "active"
        else InlineKeyboardButton("▶️ Увімкнути", callback_data=f"rec:{pid}:resume")
    )
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👁 Як виглядатиме", callback_data=f"rec:{pid}:show"),
                toggle,
            ],
            [
                InlineKeyboardButton("✏️ Текст", callback_data=f"rec:{pid}:edit_text"),
                InlineKeyboardButton("🖼 Фото / файл", callback_data=f"rec:{pid}:edit_media"),
            ],
            [
                InlineKeyboardButton("📅 Дні", callback_data=f"rec:{pid}:edit_days"),
                InlineKeyboardButton("🕙 Час", callback_data=f"rec:{pid}:edit_time"),
            ],
            [InlineKeyboardButton("🗑 Видалити", callback_data=f"rec:{pid}:del")],
        ]
    )


def _delete_confirm_keyboard(pid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Так, видалити", callback_data=f"rec:{pid}:del_yes"),
                InlineKeyboardButton("Ні", callback_data=f"rec:{pid}:del_no"),
            ]
        ]
    )


def message_view(conn, post, timezone: str) -> tuple[str, InlineKeyboardMarkup]:
    tz = ZoneInfo(timezone)
    who = _authors(conn, post)
    text = post["text"] or "(без підпису)"
    if post["status"] == "deleted":
        return f"🗑 Регулярне повідомлення видалено ({schedule_label(post)}){who}:\n\n{text}", (
            _EMPTY_KEYBOARD
        )
    lines = [f"🔁 Регулярне повідомлення: {schedule_label(post)}{who}"]
    if post["days"] == WORKDAYS:
        lines.append("Робочі дні — пн–пт, з урахуванням позначок 🌴 / 💼.")
    media = _media_line(post)
    if media:
        lines.append(media)
    if post["status"] == "paused":
        lines.append("⏸ На паузі — не публікується.")
    else:
        upcoming = next_run(conn, post, datetime.now(tz))
        if upcoming is not None:
            lines.append(f"Наступна публікація: {_when_label(upcoming)}.")
    if post["last_sent_at"]:
        last = datetime.fromisoformat(post["last_sent_at"]).astimezone(tz)
        lines.append(f"Востаннє опубліковано: {_when_label(last)}.")
    return "\n".join(lines) + f"\n\n{text}", _keyboard(post)


async def _broadcast(context: ContextTypes.DEFAULT_TYPE, recurring_post_id: int) -> None:
    """Як `custom._broadcast`: шле/оновлює вигляд у ВСІХ адмінів."""
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    post = db.get_recurring_post(conn, recurring_post_id)
    if post is None:
        return
    body, keyboard = message_view(conn, post, config.timezone)
    existing = {
        r["chat_id"]: r["message_id"] for r in db.recurring_post_previews(conn, recurring_post_id)
    }
    for admin_id in config.admin_user_ids:
        if admin_id in existing:
            try:
                await context.bot.edit_message_text(
                    chat_id=admin_id,
                    message_id=existing[admin_id],
                    text=body,
                    reply_markup=keyboard,
                )
            except TelegramError as exc:
                logger.error(
                    "Не вдалося оновити регулярне повідомлення chat_id=%s: %s", admin_id, exc
                )
            continue
        try:
            sent = await context.bot.send_message(
                chat_id=admin_id, text=body, reply_markup=keyboard
            )
        except TelegramError as exc:
            logger.error("Не вдалося надіслати регулярне повідомлення адміну %s: %s", admin_id, exc)
            continue
        db.add_recurring_post_preview(conn, recurring_post_id, admin_id, sent.message_id)


async def _send_post(bot, chat_id: int, post):
    """Фото й підпис — одним повідомленням (як вручну в General)."""
    caption = post["text"] or None
    if post["media_type"] == "photo":
        return await bot.send_photo(chat_id=chat_id, photo=post["file_id"], caption=caption)
    if post["media_type"] == "document":
        return await bot.send_document(chat_id=chat_id, document=post["file_id"], caption=caption)
    return await bot.send_message(chat_id=chat_id, text=post["text"])


# --- день тижня: інлайн-вибір ---------------------------------------------------------


def _days_keyboard(days: str) -> InlineKeyboardMarkup:
    explicit = "" if days == WORKDAYS else days
    toggles = [
        InlineKeyboardButton(
            f"✅{name}" if str(i) in explicit else name, callback_data=f"rec:days:{i}"
        )
        for i, name in enumerate(_WEEKDAYS)
    ]
    mark = "✅ " if days == WORKDAYS else ""
    return InlineKeyboardMarkup(
        [
            toggles,
            [
                InlineKeyboardButton(f"{mark}💼 Робочі дні", callback_data="rec:days:wd"),
                InlineKeyboardButton("Пн–Пт", callback_data="rec:days:monfri"),
                InlineKeyboardButton("Щодня", callback_data="rec:days:all"),
            ],
            [InlineKeyboardButton("Далі ➡️", callback_data="rec:days:ok")],
        ]
    )


def _days_prompt(days: str) -> str:
    chosen = days_label(days) if days else "не обрано"
    return (
        "У які дні публікувати? Натисніть дні або оберіть варіант нижче, тоді «Далі».\n"
        "💼 Робочі дні — пн–пт, але не в дні, позначені 🌴 вихідними (свята), "
        "і так само в суботу/неділю, позначені 💼 робочими.\n\n"
        f"Обрано: {chosen}"
    )


def _toggle_day(days: str, choice: str) -> str:
    if choice == "wd":
        return WORKDAYS
    if choice == "monfri":
        return _MON_FRI
    if choice == "all":
        return _EVERY_DAY
    explicit = _MON_FRI if days == WORKDAYS else days
    if choice in explicit:
        return explicit.replace(choice, "")
    return "".join(sorted(explicit + choice))


async def _ask_days(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    draft = context.user_data[_DRAFT_KEY]
    draft.setdefault("days", WORKDAYS)
    context.user_data[_STEP_KEY] = "pick_days"
    await context.bot.send_message(
        chat_id=user_id,
        text=_days_prompt(draft["days"]),
        reply_markup=_days_keyboard(draft["days"]),
    )


# --- вхідні точки -----------------------------------------------------------------------


def _new_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("➕ Нове регулярне повідомлення", callback_data="rec:new")]]
    )


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Список регулярних повідомлень — свіжа копія кожного з кнопками
    (стає «живою» для цього адміна, як у «🗓 Заплановані»)."""
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return
    conn = context.bot_data["conn"]
    chat_id = update.effective_user.id
    posts = db.recurring_posts(conn)
    if not posts:
        await update.effective_message.reply_text(
            "🔁 Регулярних повідомлень ще немає. Вони публікуються в General "
            "самі — в обрані дні о фіксованому часі (текст, фото чи файл з підписом).",
            reply_markup=_new_keyboard(),
        )
        return
    await update.effective_message.reply_text(
        f"🔁 Регулярні повідомлення: {len(posts)}. Керуйте кнопками під кожним.",
        reply_markup=_new_keyboard(),
    )
    for post in posts:
        body, keyboard = message_view(conn, post, config.timezone)
        try:
            sent = await context.bot.send_message(chat_id=chat_id, text=body, reply_markup=keyboard)
        except TelegramError as exc:
            logger.error("Не вдалося надіслати регулярне повідомлення id=%s: %s", post["id"], exc)
            continue
        db.add_recurring_post_preview(conn, post["id"], chat_id, sent.message_id)


def _content_error(text: str, has_media: bool) -> str | None:
    if has_media and len(text) > _CAPTION_LIMIT:
        return (
            f"Підпис задовгий: {len(text)} символів, Telegram дозволяє до "
            f"{_CAPTION_LIMIT}. Скоротіть і надішліть ще раз."
        )
    if len(text) > _TEXT_LIMIT:
        return f"Текст задовгий: {len(text)} символів, максимум {_TEXT_LIMIT}."
    if not has_media and not text.strip():
        return "Без фото чи файлу потрібен текст."
    return None


def _clear_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(_STEP_KEY, None)
    context.user_data.pop(_DRAFT_KEY, None)


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


async def _save_edit(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pid: int, action: str, **fields
) -> None:
    conn = context.bot_data["conn"]
    _clear_state(context)
    db.update_recurring_post(
        conn, pid, updated_by=update.effective_user.id, updated_at=_now_utc(), **fields
    )
    post = db.get_recurring_post(conn, pid)
    audit.log(conn, update.effective_user.id, f"{action} регулярного повідомлення {_short(post)}")
    await _broadcast(context, pid)


def _media_from(message) -> tuple[str, str, str | None] | None:
    """(media_type, file_id, file_name) з повідомлення адміна."""
    if message.photo:
        return "photo", message.photo[-1].file_id, None
    if message.document is not None:
        return "document", message.document.file_id, message.document.file_name
    return None


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Фото чи файл — лише на кроках, де їх чекаємо (інакше бот мовчить)."""
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    step = context.user_data.get(_STEP_KEY)
    if step not in ("await_content", "await_edit_media"):
        return
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return
    message = update.effective_message
    media = _media_from(message)
    if media is None:
        return
    media_type, file_id, file_name = media
    caption = message.caption or ""
    draft = context.user_data.setdefault(_DRAFT_KEY, {})

    if step == "await_content":
        error = _content_error(caption, has_media=True)
        if error:
            await message.reply_text(error)
            return
        draft.update(media_type=media_type, file_id=file_id, file_name=file_name, text=caption)
        if caption:
            await _ask_days(context, update.effective_user.id)
            return
        context.user_data[_STEP_KEY] = "await_caption"
        await message.reply_text(
            f"Надішліть підпис до {'фото' if media_type == 'photo' else 'файлу'} "
            f"(або «{_NO_CAPTION}» — без підпису)."
        )
        return

    conn = context.bot_data["conn"]
    pid = draft["edit_id"]
    post = db.get_recurring_post(conn, pid)
    if post is None or post["status"] == "deleted":
        _clear_state(context)
        await message.reply_text("Це регулярне повідомлення вже видалено.")
        return
    # Фото з підписом під час заміни фото — замінюємо й підпис.
    text = caption or post["text"]
    error = _content_error(text, has_media=True)
    if error:
        await message.reply_text(error)
        return
    fields = {"media_type": media_type, "file_id": file_id, "file_name": file_name}
    if caption:
        fields["text"] = caption
    await _save_edit(update, context, pid, "змінив фото / файл", **fields)
    await message.reply_text("Фото й підпис оновлено." if caption else "Фото / файл оновлено.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    step = context.user_data.get(_STEP_KEY)
    if not step:
        return
    config: Config = context.bot_data["config"]
    if not await ensure_admin(update, config):
        return

    conn = context.bot_data["conn"]
    message = update.effective_message
    text = message.text or ""
    draft = context.user_data.setdefault(_DRAFT_KEY, {})

    if step == "await_content":
        error = _content_error(text, has_media=False)
        if error:
            await message.reply_text(error)
            return
        draft.update(media_type=None, file_id=None, file_name=None, text=text)
        await _ask_days(context, update.effective_user.id)
        return

    if step == "await_caption":
        caption = "" if text.strip() == _NO_CAPTION else text
        error = _content_error(caption, has_media=True)
        if error:
            await message.reply_text(error)
            return
        draft["text"] = caption
        await _ask_days(context, update.effective_user.id)
        return

    if step == "pick_days":
        await message.reply_text("Оберіть дні кнопками вище й натисніть «Далі ➡️».")
        return

    if step == "await_time":
        parsed = _parse_hhmm(text)
        if parsed is None:
            await message.reply_text("Невірний формат. Спробуйте ще: гг:хх, наприклад 10:00.")
            return
        _clear_state(context)
        now = _now_utc()
        pid = db.create_recurring_post(
            conn,
            text=draft["text"],
            media_type=draft["media_type"],
            file_id=draft["file_id"],
            file_name=draft["file_name"],
            days=draft["days"],
            post_time=parsed.strftime("%H:%M"),
            created_by=update.effective_user.id,
            created_at=now,
        )
        post = db.get_recurring_post(conn, pid)
        audit.log(
            conn,
            update.effective_user.id,
            f"створив регулярне повідомлення {_short(post)} ({schedule_label(post)})",
        )
        await _broadcast(context, pid)
        upcoming = next_run(conn, post, datetime.now(ZoneInfo(config.timezone)))
        first = f" Перша публікація: {_when_label(upcoming)}." if upcoming else ""
        await message.reply_text(
            f"Готово: {schedule_label(post)}.{first} Перевірити вигляд — «👁 Як виглядатиме»."
        )
        return

    pid = draft.get("edit_id")
    post = db.get_recurring_post(conn, pid) if pid is not None else None
    if post is None or post["status"] == "deleted":
        _clear_state(context)
        await message.reply_text("Це регулярне повідомлення вже видалено.")
        return
    has_media = post["media_type"] is not None

    if step == "await_edit_text":
        new_text = "" if has_media and text.strip() == _NO_CAPTION else text
        error = _content_error(new_text, has_media)
        if error:
            await message.reply_text(error)
            return
        await _save_edit(update, context, pid, "змінив текст", text=new_text)
        await message.reply_text("Текст оновлено.")
        return

    if step == "await_edit_media":
        if text.strip() != _NO_CAPTION:
            await message.reply_text(
                f"Надішліть фото або файл (або «{_NO_CAPTION}» — прибрати фото / файл)."
            )
            return
        if not post["text"].strip():
            await message.reply_text(
                "Без фото чи файлу потрібен текст — спершу змініть текст (✏️ Текст)."
            )
            return
        await _save_edit(
            update,
            context,
            pid,
            "прибрав фото / файл",
            media_type=None,
            file_id=None,
            file_name=None,
        )
        await message.reply_text("Фото / файл прибрано — публікуватиметься лише текст.")
        return

    if step == "await_edit_time":
        parsed = _parse_hhmm(text)
        if parsed is None:
            await message.reply_text("Невірний формат. Спробуйте ще: гг:хх, наприклад 10:00.")
            return
        label = parsed.strftime("%H:%M")
        await _save_edit(
            update, context, pid, "змінив час", post_time=label, schedule_set_at=_now_utc()
        )
        await message.reply_text(f"Час оновлено: {label}.")
        return


_EDIT_PROMPTS = {
    "edit_text": "Надішліть новий текст{caption_hint}. /cancel — скасувати.",
    "edit_media": (
        "Надішліть нове фото або файл (можна з підписом — тоді заміниться й текст), "
        f"або «{_NO_CAPTION}» — прибрати фото / файл. /cancel — скасувати."
    ),
    "edit_time": "Введіть новий час публікації: гг:хх, наприклад 10:00. /cancel — скасувати.",
}


async def _on_days_action(update: Update, context: ContextTypes.DEFAULT_TYPE, choice: str) -> None:
    query = update.callback_query
    draft = context.user_data.get(_DRAFT_KEY)
    if context.user_data.get(_STEP_KEY) != "pick_days" or draft is None:
        await query.answer("Вибір днів уже неактивний")
        try:
            await query.edit_message_reply_markup(reply_markup=_EMPTY_KEYBOARD)
        except TelegramError:
            pass
        return

    if choice != "ok":
        draft["days"] = _toggle_day(draft["days"], choice)
        await query.answer()
        try:
            await query.edit_message_text(
                _days_prompt(draft["days"]), reply_markup=_days_keyboard(draft["days"])
            )
        except TelegramError:
            pass
        return

    if not draft["days"]:
        await query.answer("Оберіть хоча б один день", show_alert=True)
        return
    await query.answer()
    label = days_label(draft["days"])
    pid = draft.get("edit_id")
    if pid is None:
        context.user_data[_STEP_KEY] = "await_time"
        try:
            await query.edit_message_text(
                f"Дні: {label}.\n\nО котрій публікувати? Формат гг:хх "
                f"(за {context.bot_data['config'].timezone}), наприклад 10:00."
            )
        except TelegramError:
            pass
        return

    post = db.get_recurring_post(context.bot_data["conn"], pid)
    if post is None or post["status"] == "deleted":
        _clear_state(context)
        try:
            await query.edit_message_text("Це регулярне повідомлення вже видалено.")
        except TelegramError:
            pass
        return
    await _save_edit(
        update, context, pid, "змінив дні", days=draft["days"], schedule_set_at=_now_utc()
    )
    try:
        await query.edit_message_text(f"Дні оновлено: {label}.")
    except TelegramError:
        pass


async def on_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    config: Config = context.bot_data["config"]
    conn = context.bot_data["conn"]
    user = update.effective_user
    if user is None or user.id not in config.admin_user_ids:
        await query.answer("Немає доступу", show_alert=True)
        return

    parts = query.data.split(":")
    if parts[1] == "new":
        input_state.clear_all(context.user_data)
        context.user_data[_STEP_KEY] = "await_content"
        context.user_data[_DRAFT_KEY] = {}
        await query.answer()
        await context.bot.send_message(
            chat_id=user.id,
            text="Надішліть, що публікувати: текст, або фото чи файл — одразу з підписом "
            "(підпис додається в полі «Додати підпис» під фото). /cancel — скасувати.",
        )
        return
    if parts[1] == "days":
        await _on_days_action(update, context, parts[2])
        return

    pid, action = int(parts[1]), parts[2]
    post = db.get_recurring_post(conn, pid)
    if post is None or post["status"] == "deleted":
        await query.answer("Вже видалено")
        if post is not None:
            body, keyboard = message_view(conn, post, config.timezone)
            try:
                await query.edit_message_text(body, reply_markup=keyboard)
            except TelegramError:
                pass
        return

    if action == "show":
        await query.answer()
        await _send_post(context.bot, user.id, post)
        await context.bot.send_message(
            chat_id=user.id,
            text=f"☝️ Так виглядатиме в General ({schedule_label(post)}).",
        )
        return

    if action in ("pause", "resume"):
        status = "paused" if action == "pause" else "active"
        fields = {"status": status}
        if action == "resume":
            # Слот, що вже минув за час паузи, не наздоганяємо.
            fields["schedule_set_at"] = _now_utc()
        db.update_recurring_post(conn, pid, updated_by=user.id, updated_at=_now_utc(), **fields)
        verb = "призупинив" if action == "pause" else "увімкнув"
        audit.log(conn, user.id, f"{verb} регулярне повідомлення {_short(post)}")
        await query.answer("На паузі" if action == "pause" else "Увімкнено")
        await _broadcast(context, pid)
        return

    if action == "edit_days":
        input_state.clear_all(context.user_data)
        context.user_data[_DRAFT_KEY] = {"edit_id": pid, "days": post["days"]}
        await query.answer()
        await _ask_days(context, user.id)
        return

    if action in _EDIT_PROMPTS:
        input_state.clear_all(context.user_data)
        context.user_data[_STEP_KEY] = f"await_{action}"
        context.user_data[_DRAFT_KEY] = {"edit_id": pid}
        hint = f" (або «{_NO_CAPTION}» — без підпису)" if post["media_type"] else ""
        await query.answer()
        await context.bot.send_message(
            chat_id=user.id, text=_EDIT_PROMPTS[action].format(caption_hint=hint)
        )
        return

    if action == "del":
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=_delete_confirm_keyboard(pid))
        except TelegramError:
            pass
        return

    if action == "del_no":
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=_keyboard(post))
        except TelegramError:
            pass
        return

    if action == "del_yes":
        db.update_recurring_post(
            conn, pid, updated_by=user.id, updated_at=_now_utc(), status="deleted"
        )
        audit.log(conn, user.id, f"видалив регулярне повідомлення {_short(post)}")
        await query.answer("Видалено")
        await _broadcast(context, pid)
        return

    logger.warning("Невідома дія регулярного повідомлення: %s", query.data)


# --- публікація -------------------------------------------------------------------------


async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, config: Config, text: str) -> None:
    for admin_id in config.admin_user_ids:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except TelegramError as exc:
            logger.error("Не вдалося повідомити адміна %s: %s", admin_id, exc)


async def job_dispatch(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Раз на хвилину: публікує все, чий час настав (`is_due`). Збій —
    повтор щохвилини в межах CATCH_UP; адмінам — одне повідомлення про
    перший збій за день і одне, якщо таки вдалось."""
    conn = context.bot_data["conn"]
    config: Config = context.bot_data["config"]
    now_local = datetime.now(ZoneInfo(config.timezone))
    today = now_local.date().isoformat()
    day_type = scheduler.determine_day_type(conn, now_local.date())
    for post in db.active_recurring_posts(conn):
        if not is_due(post, now_local, day_type):
            continue
        label = f"{_short(post)}, {schedule_label(post)}"
        try:
            await _send_post(context.bot, config.group_chat_id, post)
        except TelegramError as exc:
            logger.error(
                "Не вдалося опублікувати регулярне повідомлення id=%s: %s", post["id"], exc
            )
            if post["publish_failed_on"] != today:
                db.set_recurring_post_failed_on(conn, post["id"], today)
                slot = datetime.combine(now_local.date(), _post_time(post))
                deadline = (slot + CATCH_UP).strftime("%H:%M")
                await _notify_admins(
                    context,
                    config,
                    f"⚠️ Не вдалося опублікувати регулярне повідомлення в General "
                    f"({label}): {exc}\nПробую ще раз щохвилини до {deadline}.",
                )
            continue
        db.mark_recurring_post_sent(conn, post["id"], today, datetime.now(UTC).isoformat())
        audit.log(conn, None, f"опубліковано регулярне повідомлення в General ({label})")
        if post["publish_failed_on"] == today:
            await _notify_admins(
                context, config, f"✅ Регулярне повідомлення ({label}) таки опубліковано в General."
            )
        await _broadcast(context, post["id"])
