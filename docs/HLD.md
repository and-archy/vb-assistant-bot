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
    threat_type TEXT,
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
    morning_date TEXT PRIMARY KEY, status TEXT,   -- pending/sent/skipped/auto_sent
    day_type TEXT, variant_set TEXT, variant_id TEXT,
    message_text TEXT, stats_json TEXT,
    created_at TEXT, resolved_at TEXT, resolved_by INTEGER
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

## [4] Ротація текстів — deck.py + message_builder.py

`deck.draw_next(conn, set_name, variants)` — перетасована колода
(Fisher–Yates), без повторів у межах проходу; при вичерпанні —
тасування заново. Три незалежні колоди (А/Б/В), стан у `deck_state`,
історія в `deck_history`.

`message_builder.build_message` — варіант + (домовленості, лише
`day_type == "workday"`) + контакти.

## [5]–[6] Прев'ю та людина в контурі — scheduler.py

`generate_and_send_preview(context, force)` — спільна точка входу для
07:01-джоби і `/support`. `force=True` обходить тригер. Обирає набір за
замовчуванням (А будні / В вихідні), тягне варіант з колоди, шле DM
усім `ADMIN_USER_IDS` з кнопками, зберігає `message_id` кожної копії
в `preview_messages`.

`on_preview_action` — гейт на `ADMIN_USER_IDS`, читає
`preview_state.status`: якщо не `pending` — «Вже оброблено» і чистить
клавіатуру. `send`/`skip` — резолвлять і чистять усі копії прев'ю у
всіх адмінів. `more`/`calm` — тягнуть новий варіант (той самий
набір / примусово Б), оновлюють `preview_state` і **лише повідомлення
адміна, що натиснув** (інші копії лишаються застарілими — відоме
обмеження v1 для команди з 2 адмінами).

## [7] Автопублікація — scheduler.job_autopublish

08:00: якщо `preview_state.status == "pending"` і `AUTO_PUBLISH_
ENABLED=true` — публікує `message_text` у `GROUP_CHAT_ID`, резолвить
`auto_sent`. Якщо вимкнено (дефолт першого місяця, ТЗ п.13) — лише
нагадує адмінам, нічого не публікує і не змінює статус (щоб кнопки
лишались активними).
