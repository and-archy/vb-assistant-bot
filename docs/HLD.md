# HLD — VB Assistant

Каскад від `docs/BRD.md`. Framework: **python-telegram-bot** (`telegram.ext`,
v21+, той самий стек, що й у `cyber_news_tg_bot`/`tg-apteka`) з
`JobQueue` для щоденних завдань і опитування alerts.in.ua,
`CallbackQueryHandler` для кнопок прев'ю.

## Component overview

```
alerts.in.ua ──poll (JobQueue.run_repeating)──▶ [1] alerts_client + timeutil
                                                        │
                                                        ▼
                                              [2] Storage (SQLite: alerts,
                                                  manual_day_type, deck_state,
                                                  deck_history, preview_state,
                                                  preview_messages, send_log)
                                                        │
                          ┌─────────────────────────────┼──────────────────────┐
                          ▼                              ▼                      ▼
              [3] night_logic (тригер)        [4] deck + message_builder   [5] scheduler
              compute_night_stats,             перетасована колода +      07:01 preview /
              is_heavy_night                    build_message              08:00 autopublish
                          │                              │                      │
                          └──────────────┬───────────────┘                      │
                                         ▼                                      ▼
                         generate_and_send_preview ──DM адмінам──▶ [6] CallbackQueryHandler
                                                                     on_preview_action (send/
                                                                     more/calm/skip)
                                                                              │
                                                                              ▼
                                                              [7] публікація в GROUP_CHAT_ID
```

`/support` (адмін-команда) викликає той самий
`generate_and_send_preview(force=True)`, обходячи тригер — ТЗ п.4.
`/markweekend`, `/markworkday` пишуть у `manual_day_type` — ТЗ п.13.

## [1] Джерело тривог — alerts_client.py + timeutil.py

`AlertsInUaClient.fetch_region_history(region_uid)` —
`GET /v1/regions/{uid}/alerts/month_ago.json`, `Authorization: Bearer
<ALERTS_API_TOKEN>`. Обрано історичний ендпоінт (не `active.json`),
бо він одразу дає `started_at`/`finished_at` — не треба самим
детектувати кінець тривоги опитуванням. У цього ендпоінту окремий,
жорсткіший ліміт — 2 запити/хв (https://devs.alerts.in.ua/,
«Обмеження») — `alerts_poll_interval_seconds` (дефолт 90с ≈ 0.67/хв)
вкладається з запасом.

Поля API розібрані через `.get()` — офіційна схема може змінитись.
`alert_type` — категорія сирени (air_raid/artillery_shelling/...), не
тип озброєння; точний тип (ТЗ п.2: БпЛА/балістика/авіація) API окремо
віддає в масиві `threats[].threat_type` (enum: `ballistic_missiles`,
`cruise_missiles`, `unspecified_missiles`, `drones`,
`tactic_aircraft_activity`, `strategic_aircraft_activity`,
`mig31k_departure`, `guided_aerial_bombs`, `air_defense`, `unknown`) —
`AlertRecord.threat_types` парсить це поле напряму, без здогадок.

**Empіричний факт (живий запит 2026-09-09, ~150 тривог за місяць для
uid 31):** поле `threats` не було присутнє **жодного разу**. Ймовірно
воно специфічне для `/v1/alerts/active.json` (документація прив'язує
приклад з `threats` саме туди), а `month_ago.json` його не віддає
взагалі. `AlertsInUaClient`/`night_logic` це переживають штатно
(порожній кортеж → `has_ballistic=False`), просто halving порогів при
балістиці (ТЗ п.3) у поточній конфігурації практично ніколи не
спрацює. Якщо після місяця роботи це виявиться важливим — доведеться
додати окреме опитування `active.json` (менш строгий ліміт, 8-10/хв)
поки тривога ще жива, і зливати його `threats` з історією
`month_ago.json` за `external_id`; наразі не реалізовано.

`timeutil.to_canonical_utc_iso` нормалізує кожен запис до фіксованого
UTC ISO8601 (`+00:00`, без мілісекунд/`Z`) **перед** записом у БД —
критично, бо `db.alerts_overlapping` фільтрує вікно ночі рядковим
порівнянням `started_at`/`finished_at` (без цього змішування `Z` і
`+00:00`/локального офсету ламає порівняння).

`scheduler.poll_alerts` — `JobQueue.run_repeating` кожні
`alerts_poll_interval_seconds` (дефолт 90с, `thresholds.json`),
апсертить у `alerts` за `external_id` (ідемпотентно).

## [2] Storage — SQLite

```sql
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT NOT NULL UNIQUE,
    location_uid TEXT NOT NULL,
    raw_alert_type TEXT,
    alert_level TEXT,              -- "red"/"yellow" — присутнє завжди, на відміну від threats
    threat_types TEXT NOT NULL DEFAULT '[]',  -- JSON-масив, на практиці завжди порожній
    started_at TEXT NOT NULL,     -- канонічний UTC ISO8601
    finished_at TEXT,             -- NULL = ще триває
    updated_at TEXT NOT NULL
);

CREATE TABLE manual_day_type (
    day TEXT PRIMARY KEY, day_type TEXT CHECK (IN workday/weekend),
    set_by INTEGER, set_at TEXT
);

CREATE TABLE deck_state (set_name TEXT PRIMARY KEY, remaining_json TEXT);
CREATE TABLE deck_history (id, set_name, variant_id, used_at);

CREATE TABLE preview_state (
    morning_date TEXT PRIMARY KEY,
    status TEXT,   -- pending/queued/skipped/published/auto_sent (2026-09-18+)
    day_type TEXT, variant_set TEXT, variant_id TEXT,
    message_text TEXT, stats_json TEXT,
    stats_intro TEXT DEFAULT '',   -- готовий текст статистики+вердикту (не парсити з чату)
    triggered INTEGER DEFAULT 0,   -- вердикт алгоритму (2026-09-17+, лише рекомендаційний)
    created_at TEXT, resolved_at TEXT, resolved_by INTEGER  -- resolved_by = хто натиснув "Надіслати"
);
CREATE TABLE preview_messages (morning_date, chat_id, message_id);  -- копія в кожного адміна

CREATE TABLE send_log (
    id, morning_date, variant_set, variant_id, mode,  -- manual/auto
    sent_by, sent_at, group_message_id
);
```

`preview_state.morning_date` — природний ключ дня (не `created_at`):
`/support`, повторний виклик того ж ранку чи повторне спрацювання
тригера перезаписують той самий рядок (`upsert_preview` скидає
`resolved_*` і `preview_messages` — щоб застаріле «вже оброблено» не
блокувало новий цикл).

## [3] Тригер — night_logic.py

`compute_night_stats` рахує вікно ночі `[D-1 22:00, D 07:00)` за
`Europe/Kyiv`, клипає кожну тривогу до меж вікна (і до `now`, якщо
тривога ще триває), рахує `count`, `total_duration`, `longest`,
`has_ballistic`, і прапорець `crosses_hard_window` на кожній тривозі
(01:00–05:00). `is_heavy_night` застосовує пороги з `thresholds.json`
(ТЗ п.3) — множник `ballistic_threshold_multiplier` на порогах
тривалості, якщо `has_ballistic`.

**Розбивка ракетна/дронова (2026-09-18):** `NightStats.missile_alerts`/
`missile_duration` (alert_level == "red") і `drone_alerts`/
`drone_duration` ("yellow") — властивості, обчислені з `stats.alerts`,
не окремі поля тригера. Показуються в `format_stats_summary` для
орієнтації адміна, **не впливають** на `is_heavy_night` — halving
порогів і далі керується `threat_types`/`has_ballistic` (інше поле,
на практиці завжди порожнє — див. BRD, «Відкрите питання»).

**З 2026-09-17 результат `is_heavy_night` більше НЕ вирішує, чи
надсилати прев'ю** (інцидент — масований обстріл не пробив пороги,
07:01-джоба нічого не надіслала). Він і далі рахується та зберігається
(`preview_state.triggered`), але лише як (а) рекомендаційний рядок у
тексті прев'ю (`formatting.format_stats_summary`) і (б) умова для
`job_autopublish` [7] — безлюдна дія має сенс лише коли алгоритм і так
рекомендував.

## [4] Ротація текстів — deck.py + message_builder.py

`deck.draw_next(conn, set_name, variants)` — перетасована колода
(Fisher–Yates), без повторів у межах проходу; при вичерпанні —
тасування заново. Три незалежні колоди (А/Б/В), стан у `deck_state`,
історія в `deck_history`.

`message_builder.build_message` — варіант + (домовленості, лише
`day_type == "workday"`) + контакти.

## [5]–[6] Прев'ю та людина в контурі — scheduler.py

`generate_and_send_preview(context, force) -> PreviewResult` — спільна
точка входу для 07:01-джоби і `/support`. **Шле прев'ю безумовно**
(2026-09-17+, тригер більше не гейтить відправку — див. [3]). Єдине,
що робить `force`:

- `force=False` (щоденна джоба): якщо на сьогодні вже є рішення
  (`preview_state.status != "pending"`) — не дублює, повертає
  `PreviewResult(generated=False)`.
- `force=True` (`/support`): завжди перегенеровує, ігноруючи попереднє
  рішення.

Обирає набір за замовчуванням (А будні / В вихідні), тягне варіант з
колоди, будує текст, шле DM усім `ADMIN_USER_IDS` з кнопками, зберігає
`message_id` кожної копії в `preview_messages`. Повертає `PreviewResult`
(скільки адмінів реально отримали DM, вердикт тригера) — і `/support`,
і джоба логують/показують цей результат, ніколи не мовчать.

### Стейт-машина `preview_state.status` (2026-09-18+)

```
pending ──send──▶ queued ──cancel──▶ pending
   │                  │
   └──skip──▶ skipped │
                       └── job_autopublish (08:00) ──▶ published
pending (triggered=1, ніхто не натиснув) ── job_autopublish ──▶ auto_sent (якщо AUTO_PUBLISH_ENABLED)
```

**Вимога 2026-09-18:** «Надіслати» більше не публікує миттєво в групу —
лише переводить `pending → queued` (зберігає `resolved_by` = хто
натиснув). Фактична публікація в `GROUP_CHAT_ID` відбувається лише на
`job_autopublish` о 08:00, незалежно від `triggered` — це дає вікно
для «Скасувати» (`queued → pending`, `resolved_by` скидається),
вибору іншого тексту/тону і повторного «Надіслати» до 8:00.

`on_preview_action` — гейт на `ADMIN_USER_IDS`, дозволені дії залежно
від `status`: `pending` → `{send, more, calm, skip}`; `queued` → `{cancel}`.
Будь-яка інша комбінація (застаріла кнопка, вже вирішено) → «Вже
оброблено» і поточний справжній стан підвантажується в повідомлення,
що клікнули.

**`_broadcast_current_view`** — після КОЖНОЇ зміни стану (send/cancel/
skip/more/calm) переписує (`edit_message_text`) повідомлення в
особистих **усіх** адмінів під нову спільну правду з `preview_state`
(текст+кнопки залежно від `status`) — не лише те, що клікнули (ТЗ:
«щоб це бачив кожен адміністратор»; заразом усуває стару відому
ваду, коли «Інший варіант»/«Стриманий тон» оновлював лише клікнуте
повідомлення).

## [7] Автопублікація/диспетчеризація — scheduler.job_autopublish

08:00, дві незалежні гілки:

1. **`status == "queued"`** (адмін уже натиснув «Надіслати» будь-коли
   до 8:00, незалежно від `triggered`) — публікує `message_text` у
   `GROUP_CHAT_ID` просто зараз, резолвить `published`, `send_log.mode
   = "manual"`, `sent_by` = той, хто натиснув. Це основний шлях з
   2026-09-18.
2. **`status == "pending"` і `triggered`** (ніхто не відреагував на
   важку ніч) — безлюдний резервний шлях: `AUTO_PUBLISH_ENABLED=false`
   (дефолт першого місяця, ТЗ п.13) — лише нагадує адмінам; `=true` —
   публікує сам, резолвить `auto_sent`, `send_log.mode = "auto"`.

`status == "pending"` і НЕ `triggered` (спокійна ніч, ніхто не
відповів) — тиша, очікуваний результат, не збій. `status == "skipped"`
— теж тиша (явне рішення адміна).

## [8] Відмовостійкість — /support і глобальний error handler

Інцидент 2026-09-17: `/support` не відповідав у чат виклику взагалі —
увесь ефект команди йшов лише в DM адмінам, і будь-яка помилка
(виняток усередині, чи недоступний DM конкретного адміна) губилась
мовчки. Виправлено на двох рівнях:

- `handlers/support.py` завжди відповідає в чат, де викликана команда:
  успіх → «Прев'ю надіслано N/M адмінам», збій → короткий текст
  помилки (обгортає виклик у `try/except`).
- `__main__.on_error`, зареєстрований через
  `application.add_error_handler`, ловить БУДЬ-яку необроблену
  помилку в будь-якому хендлері чи джобі й шле всім
  `ADMIN_USER_IDS` `⚠️ Помилка в боті: ...` — друга лінія захисту від
  тиші там, де конкретний хендлер сам не подбав про це.

## [9] Своє повідомлення — handlers/custom.py

Не ConversationHandler (щоб не додавати ще одну абстракцію заради
двох кроків) — легкий стейт у `context.user_data["custom_step"]`
(`await_text → await_time`, або `await_edit_text`/`await_edit_time`
при редагуванні), який читає єдиний глобальний `MessageHandler(filters
.TEXT & ~filters.COMMAND, custom.on_text)` — ігнорує будь-який текст
без активного кроку чи поза приватним чатом (`effective_chat.type !=
"private"`, щоб не реагувати на випадкові повідомлення в General).

`custom_messages` — власна таблиця, незалежна від `preview_state`:
`status` ∈ `{scheduled, cancelled, sent}`. `job_dispatch` (кожні 60с,
`scheduler.register`) публікує все, де `scheduled_at <= now` — точність
до хвилини достатня для довільно обраного адміном часу (на відміну від
фіксованих 07:01/08:00 ранкового циклу, де `run_daily` доречніший).

`_broadcast` — той самий патерн, що й `scheduler._broadcast_current_view`:
після кожної зміни (створення/скасування/редагування тексту чи часу)
переписує вигляд у особистих **усіх** адмінів (`custom_message_previews`
зберігає `message_id` кожної копії), не лише того, хто діяв.
