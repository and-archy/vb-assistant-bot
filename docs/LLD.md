# LLD — VB Assistant

Каскад від `docs/HLD.md`. Схема БД — уже в HLD [2], тут не дублюється.

## Module layout

```
src/vb_assistant_bot/
    __init__.py
    __main__.py
    config.py          # .env -> Config (секрети/ідентифікатори)
    content.py          # config/texts.json, config/thresholds.json -> Texts, Thresholds
    db.py                # SQLite schema + CRUD
    timeutil.py          # парсинг/нормалізація ISO8601 з alerts.in.ua
    alerts_client.py     # AlertsInUaClient
    night_logic.py        # compute_night_stats, is_heavy_night
    deck.py                # перетасована колода
    message_builder.py     # build_message
    formatting.py           # format_stats_summary (текст прев'ю)
    access.py                # ensure_admin, remember_admin, admin_label, display_name
    scheduler.py              # jobs + generate_and_send_preview + on_preview_action
    health.py                  # серія помилок + job_check_recovery («✅ знову працює»)
    alerts_health.py            # свіжість даних alerts.in.ua (bot_state)
    audit.py                     # action_log: log, format_history
    input_state.py                # ключі user_data вводу + clear_all
    keyboards.py                   # кнопки під опублікованим у General
    text_store.py                   # texts.json + text_variants -> effective Texts
    whats_new.py                     # одноразове «Що нового» адмінам
    handlers/
        menu.py               # /start /help /cancel + кнопкове меню
        support.py             # /support
        weekend.py              # /markweekend /markworkday
        custom.py                # /custom — своє повідомлення на власну дату/час
        published.py              # pub:* — виправити/видалити в General
        texts.py                   # /texts, txt:* — керування текстами
        overview.py                 # /status /scheduled /history
```

## Контракти

```python
# config.py
@dataclass(frozen=True)
class Config:
    token: str
    admin_user_ids: frozenset[int]   # мінімум 2, RuntimeError інакше
    group_chat_id: int
    alerts_api_token: str
    alerts_region_uid: str            # дефолт "31"
    db_path: str
    timezone: str                      # дефолт "Europe/Kyiv"
    texts_config_path: str
    thresholds_config_path: str
    auto_publish_enabled: bool          # дефолт True (з 2026-09-24)

def load_config() -> Config: ...        # .env через python-dotenv
```

```python
# content.py
@dataclass(frozen=True)
class Variant:
    id: str
    text: str

@dataclass(frozen=True)
class Texts:
    weekday_arrangements: str
    contacts: str
    sets: dict[str, tuple[Variant, ...]]   # "A" (10) / "B" (4) / "V" (3)

@dataclass(frozen=True)
class Thresholds:
    night_window_start: time; night_window_end: time
    hard_window_start: time; hard_window_end: time
    hard_window_min_duration_minutes: int
    min_alerts_count: int
    min_total_duration_minutes: int
    min_single_alert_minutes: int
    ballistic_threshold_multiplier: float
    ballistic_threat_types: frozenset[str]   # звіряється з AlertWindow.threat_types
    preview_time: time; autopublish_time: time
    alerts_poll_interval_seconds: int

def load_texts(path: str) -> Texts: ...
def load_thresholds(path: str) -> Thresholds: ...
```

```python
# night_logic.py
@dataclass(frozen=True)
class AlertWindow:
    started_at: datetime; finished_at: datetime
    threat_types: tuple[str, ...]   # напряму з alerts.in.ua threats[].threat_type
    ongoing: bool; crosses_hard_window: bool
    @property
    def duration(self) -> timedelta: ...

@dataclass(frozen=True)
class NightStats:
    morning_date: date; window_start: datetime; window_end: datetime
    alerts: tuple[AlertWindow, ...]; total_duration: timedelta
    longest: AlertWindow | None; has_ballistic: bool
    @property
    def count(self) -> int: ...

def compute_night_stats(conn, *, location_uid, morning_date, timezone,
                         thresholds, now=None) -> NightStats: ...
def is_heavy_night(stats: NightStats, thresholds: Thresholds) -> bool: ...
```

```python
# deck.py
def draw_next(conn, set_name: str, variants: tuple[Variant, ...]) -> Variant: ...
```

```python
# scheduler.py
@dataclass(frozen=True)
class PreviewResult:
    generated: bool          # False лише коли сьогодні вже оброблено (force=False), не помилка
    sent_to: int = 0
    admin_count: int = 0
    triggered: bool = False  # вердикт is_heavy_night — лише рекомендаційний з 2026-09-17

def determine_day_type(conn, morning_date: date) -> str: ...       # "workday"/"weekend"
async def poll_alerts(context) -> None: ...
async def generate_and_send_preview(context, *, force: bool) -> PreviewResult: ...  # шле завжди
async def job_preview(context) -> None: ...
async def job_autopublish(context) -> None: ...
# autopublish_time (7:30): лише коли status=="pending" AND triggered ->
# нагадування/автопублікація; у суботу/неділю (is_calendar_weekend) — лише нагадування. "queued" тут НЕ обробляється (2026-09-20+).
async def job_publish_queued(context) -> None: ...
# поллер (раз/хв, як custom.job_dispatch): публікує status=="queued" AND
# scheduled_at <= now — незалежно від triggered; сам scheduled_at обирає
# адмін через send/send_now/send_fixed/send_custom.
async def on_preview_action(update, context) -> None: ...          # callback_data "prev:<date>:<action>"
# action залежить від status:
#   pending -> {send, send_now, send_fixed, send_custom, send_back, more, calm, skip}
#   queued  -> {cancel}
# "send" лише показує підменю часу (не міняє status); send_now/send_fixed/
# send_custom ставлять status="queued" з конкретним scheduled_at.
```

```python
# __main__.py
async def on_error(update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
# зареєстрований через application.add_error_handler — будь-яка
# необроблена помилка шле ADMIN_USER_IDS "⚠️ Помилка в боті: ...".
```

## Callback data формат

`prev:<morning_date ISO>:<action>`, `<action>` ∈
`{send, send_now, send_fixed, send_custom, send_back, more, calm, skip,
cancel}` — дозволені залежно від поточного `status` (див. scheduler.py
вище). `send` (2026-09-20+) не публікує й не міняє `status` — лише
показує підменю часу; `send_now`/`send_fixed`/`send_custom` переводять
`pending → queued` із конкретним `scheduled_at` (зараз / `autopublish_time`,
за замовч. 7:30 / довільний сьогоднішній час), фактична публікація —
`job_publish_queued` (поллер, раз/хв), не єдина добова джоба. `send_back`
повертає до звичайного вигляду `pending` без змін у БД.

## Callback data формат — опубліковане (2026-10-05+)

`pub:<prev|custom>:<morning_date|custom id>:<action>`, `<action>` ∈
`{edit, del, del_yes, del_no}`. Дозволено лише для
`preview_state.status ∈ {published, auto_sent}` /
`custom_messages.status == "sent"` з `group_message_id IS NOT NULL`,
інакше «Вже неактуально».

## Callback data формат — тексти (2026-10-05+)

`txt:sets`, `txt:set:<A|V|B>`, `txt:add:<set>`,
`txt:<var|edit|toggle|reset|del>:<variant_id>`. `add`/`edit` ставлять
`user_data["texts_step"] = "add:<set>" | "edit:<id>"`, текст — наступним
повідомленням (`texts.on_text`, група 3). `reset` — лише для варіантів
із файлу, `del` — лише для доданих у боті.

## Callback data формат — /custom

`custom:<id>:<action>`, `<action>` ∈ `{cancel, edit_text, edit_time}`.
Дозволено лише коли `custom_messages.status == "scheduled"`.
`edit_text`/`edit_time` не міняють нічого одразу — переводять
`context.user_data["custom_step"]` у `await_edit_text`/`await_edit_time`,
фактична зміна — наступним текстовим повідомленням від того ж адміна.

## Команди

| Команда | Доступ | Дія |
|---|---|---|
| `/start`, `/help` | адміни | довідка |
| `/support` | адміни | `generate_and_send_preview(force=True)`, завжди відповідає в чат виклику |
| `/markweekend [дд.мм.рррр]` | адміни | `manual_day_type[day] = "weekend"` |
| `/markworkday [дд.мм.рррр]` | адміни | `manual_day_type[day] = "workday"` |
| `/custom` | адміни | старт компоновки свого повідомлення (текст → дата/час) |
| `/cancel` | адміни | `menu.cancel` — скидає будь-який незавершений ввід (`input_state.clear_all`) |
| `/status` | адміни | `overview.status` — стан бота одним екраном |
| `/scheduled` | адміни | `overview.scheduled` — копії всіх запланованих з кнопками |
| `/history` | адміни | `overview.history` — `action_log` за 7 днів |
| `/texts` | адміни | `texts.show` — керування варіантами текстів |

Групу General бот не гейтить — жодних `MessageHandler` на текст
учасників, лише `bot.send_message(GROUP_CHAT_ID, ...)`. Виняток —
два `MessageHandler` на приватні чати: спершу `menu.on_button`
(`filters.Text(BUTTON_LABELS)`, кнопки нижче), тоді `custom.on_text`
(`filters.TEXT & ~filters.COMMAND`, лише коли є активний
`custom_step` — інакше нічого не робить).

## Кнопкове меню — handlers/menu.py

```python
BTN_SUPPORT = "🌅 Прев'ю зараз"      # -> support.support
BTN_CUSTOM = "📝 Своє повідомлення"   # -> custom.start
BTN_SCHEDULED = "🗓 Заплановані"      # -> overview.scheduled
BTN_STATUS = "📋 Статус"              # -> overview.status
BTN_WEEKEND = "🌴 Вихідний сьогодні"  # -> weekend.mark_weekend, context.args=[]
BTN_WORKDAY = "💼 Робочий сьогодні"   # -> weekend.mark_workday, context.args=[]
BTN_HISTORY = "🕘 Історія"            # -> overview.history
BTN_TEXTS = "✏️ Тексти"               # -> texts.show
BTN_CANCEL = "❌ Скасувати"           # -> menu.cancel
BTN_HELP = "❓ Довідка"               # -> menu.start (повторно)

MAIN_KEYBOARD: ReplyKeyboardMarkup  # /start, /help і whats_new.job_announce
```

`on_button` для будь-якої кнопки, крім `BTN_CANCEL`, спершу викликає
`input_state.clear_all` (і `custom.start` сам починає з чистого стану).

## Групи хендлерів (__main__.py)

| Група | Хендлер | Навіщо |
|---|---|---|
| -1 | `TypeHandler(Update, access.remember_admin)` | нікнейм адміна в `admin_names` |
| 0 | команди, callback-и, `menu.on_button`, `custom.on_text` | основна обробка |
| 1 | `scheduler.on_text` | довільний час публікації прев'ю |
| 2 | `published.on_text` | новий текст для повідомлення в General |
| 3 | `texts.on_text` | текст нового/зміненого варіанта |

Джоби поверх `scheduler.register`: `health.job_check_recovery` (раз/хв),
`whats_new.job_announce` (`run_once`, 10 с).
