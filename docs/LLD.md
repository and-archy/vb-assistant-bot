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
    night_logic.py        # compute_night_stats, is_heavy_night, match_threat_type
    deck.py                # перетасована колода
    message_builder.py     # build_message
    formatting.py           # format_stats_summary (текст прев'ю)
    access.py                # ensure_admin
    scheduler.py              # jobs + generate_and_send_preview + on_preview_action
    handlers/
        menu.py               # /start /help
        support.py             # /support
        weekend.py              # /markweekend /markworkday
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
    auto_publish_enabled: bool          # дефолт False

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
    ballistic_threat_types: frozenset[str]
    threat_type_keywords: dict[str, tuple[str, ...]]
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
    threat_type: str | None; ongoing: bool; crosses_hard_window: bool
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
def match_threat_type(raw_alert_type: str | None,
                       keywords: dict[str, tuple[str, ...]]) -> str | None: ...
```

```python
# deck.py
def draw_next(conn, set_name: str, variants: tuple[Variant, ...]) -> Variant: ...
```

```python
# scheduler.py
def determine_day_type(conn, morning_date: date) -> str: ...       # "workday"/"weekend"
async def poll_alerts(context) -> None: ...
async def generate_and_send_preview(context, *, force: bool) -> bool: ...
async def job_preview(context) -> None: ...
async def job_autopublish(context) -> None: ...
async def on_preview_action(update, context) -> None: ...          # callback_data "prev:<date>:<action>"
```

## Callback data формат

`prev:<morning_date ISO>:<action>`, `<action>` ∈
`{send, more, calm, skip}`. Немає окремого підтвердження перед `send`
(на відміну від видалення в `tg-apteka`) — навмисно, бо кожна секунда
затримки о 7 ранку небайдужа, і кнопка вже стоїть після явного
прев'ю з повним текстом.

## Команди

| Команда | Доступ | Дія |
|---|---|---|
| `/start`, `/help` | адміни | довідка |
| `/support` | адміни | `generate_and_send_preview(force=True)` |
| `/markweekend [дд.мм.рррр]` | адміни | `manual_day_type[day] = "weekend"` |
| `/markworkday [дд.мм.рррр]` | адміни | `manual_day_type[day] = "workday"` |

Групу General бот не гейтить — жодних `MessageHandler` на текст
учасників, лише `bot.send_message(GROUP_CHAT_ID, ...)`.
