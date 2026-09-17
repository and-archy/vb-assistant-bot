import json
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id   TEXT NOT NULL UNIQUE,
    location_uid  TEXT NOT NULL,
    raw_alert_type TEXT,
    alert_level   TEXT,
    threat_types  TEXT NOT NULL DEFAULT '[]',
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_location_started
    ON alerts (location_uid, started_at);

-- Ручне позначення дати як вихідної/робочої (ТЗ п.13 — свята).
CREATE TABLE IF NOT EXISTS manual_day_type (
    day       TEXT PRIMARY KEY,
    day_type  TEXT NOT NULL CHECK (day_type IN ('workday', 'weekend')),
    set_by    INTEGER,
    set_at    TEXT NOT NULL
);

-- Перетасована колода на набір текстів (ТЗ п.11): список id, що лишились
-- у поточному проході, повторна тасовка коли порожньо.
CREATE TABLE IF NOT EXISTS deck_state (
    set_name       TEXT PRIMARY KEY,
    remaining_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deck_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    set_name   TEXT NOT NULL,
    variant_id TEXT NOT NULL,
    used_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preview_state (
    morning_date  TEXT PRIMARY KEY,
    status        TEXT NOT NULL,
    day_type      TEXT NOT NULL,
    variant_set   TEXT NOT NULL,
    variant_id    TEXT NOT NULL,
    message_text  TEXT NOT NULL,
    stats_json    TEXT NOT NULL,
    stats_intro   TEXT NOT NULL DEFAULT '',
    triggered     INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    resolved_at   TEXT,
    resolved_by   INTEGER
);

CREATE TABLE IF NOT EXISTS preview_messages (
    morning_date TEXT NOT NULL REFERENCES preview_state (morning_date) ON DELETE CASCADE,
    chat_id      INTEGER NOT NULL,
    message_id   INTEGER NOT NULL,
    PRIMARY KEY (morning_date, chat_id)
);

CREATE TABLE IF NOT EXISTS send_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    morning_date      TEXT NOT NULL,
    variant_set       TEXT NOT NULL,
    variant_id        TEXT NOT NULL,
    mode              TEXT NOT NULL,
    sent_by           INTEGER,
    sent_at           TEXT NOT NULL,
    group_message_id  INTEGER
);

-- Довільне повідомлення адміна на власно обрану дату/час (не прив'язане
-- до ранкового циклу preview_state) — /custom.
CREATE TABLE IF NOT EXISTS custom_messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    text              TEXT NOT NULL,
    scheduled_at      TEXT NOT NULL,   -- канонічний UTC ISO8601
    status            TEXT NOT NULL,   -- scheduled/cancelled/sent
    created_by        INTEGER NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    sent_at           TEXT
);

CREATE TABLE IF NOT EXISTS custom_message_previews (
    custom_message_id INTEGER NOT NULL REFERENCES custom_messages (id) ON DELETE CASCADE,
    chat_id           INTEGER NOT NULL,
    message_id        INTEGER NOT NULL,
    PRIMARY KEY (custom_message_id, chat_id)
);
"""


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    # Міграції для БД, створених до появи цих колонок — CREATE TABLE IF NOT
    # EXISTS у _SCHEMA не чіпає вже існуючі таблиці, тому нові колонки
    # довелось додавати вручну (2026-09-18 — на проді впала помилка
    # "table alerts has no column named threat_types", бо ця міграція
    # спершу забулась зовсім).
    if not _column_exists(conn, "alerts", "threat_types"):
        conn.execute("ALTER TABLE alerts ADD COLUMN threat_types TEXT NOT NULL DEFAULT '[]'")
    if not _column_exists(conn, "preview_state", "triggered"):
        conn.execute("ALTER TABLE preview_state ADD COLUMN triggered INTEGER NOT NULL DEFAULT 0")
    if not _column_exists(conn, "preview_state", "stats_intro"):
        conn.execute("ALTER TABLE preview_state ADD COLUMN stats_intro TEXT NOT NULL DEFAULT ''")
    if not _column_exists(conn, "alerts", "alert_level"):
        conn.execute("ALTER TABLE alerts ADD COLUMN alert_level TEXT")
    conn.commit()
    return conn


# --- alerts -----------------------------------------------------------------


def upsert_alert(
    conn: sqlite3.Connection,
    *,
    external_id: str,
    location_uid: str,
    raw_alert_type: str | None,
    alert_level: str | None,
    threat_types: list[str],
    started_at: str,
    finished_at: str | None,
    updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO alerts
            (external_id, location_uid, raw_alert_type, alert_level, threat_types,
             started_at, finished_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (external_id) DO UPDATE SET
            raw_alert_type = excluded.raw_alert_type,
            alert_level    = excluded.alert_level,
            threat_types   = excluded.threat_types,
            started_at     = excluded.started_at,
            finished_at    = excluded.finished_at,
            updated_at     = excluded.updated_at
        """,
        (
            external_id,
            location_uid,
            raw_alert_type,
            alert_level,
            json.dumps(threat_types),
            started_at,
            finished_at,
            updated_at,
        ),
    )
    conn.commit()


def alerts_overlapping(
    conn: sqlite3.Connection, location_uid: str, window_start: str, window_end: str
) -> list[sqlite3.Row]:
    """Тривоги locations_uid, що перетинаються з [window_start, window_end)
    (порівняння рядків ISO8601 UTC — коректно, бо формат фіксованої довжини).
    Тривога без finished_at вважається такою, що триває дотепер."""
    return conn.execute(
        """
        SELECT * FROM alerts
        WHERE location_uid = ?
          AND started_at < ?
          AND (finished_at IS NULL OR finished_at > ?)
        ORDER BY started_at
        """,
        (location_uid, window_end, window_start),
    ).fetchall()


# --- manual day type ----------------------------------------------------------


def get_manual_day_type(conn: sqlite3.Connection, day: str) -> str | None:
    row = conn.execute("SELECT day_type FROM manual_day_type WHERE day = ?", (day,)).fetchone()
    return row["day_type"] if row else None


def set_manual_day_type(
    conn: sqlite3.Connection, day: str, day_type: str, set_by: int, set_at: str
) -> None:
    conn.execute(
        """
        INSERT INTO manual_day_type (day, day_type, set_by, set_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (day) DO UPDATE SET
            day_type = excluded.day_type, set_by = excluded.set_by, set_at = excluded.set_at
        """,
        (day, day_type, set_by, set_at),
    )
    conn.commit()


def clear_manual_day_type(conn: sqlite3.Connection, day: str) -> None:
    conn.execute("DELETE FROM manual_day_type WHERE day = ?", (day,))
    conn.commit()


# --- deck ---------------------------------------------------------------------


def get_deck_remaining(conn: sqlite3.Connection, set_name: str) -> list[str] | None:
    row = conn.execute(
        "SELECT remaining_json FROM deck_state WHERE set_name = ?", (set_name,)
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["remaining_json"])


def save_deck_remaining(conn: sqlite3.Connection, set_name: str, remaining: list[str]) -> None:
    conn.execute(
        """
        INSERT INTO deck_state (set_name, remaining_json) VALUES (?, ?)
        ON CONFLICT (set_name) DO UPDATE SET remaining_json = excluded.remaining_json
        """,
        (set_name, json.dumps(remaining)),
    )
    conn.commit()


def add_deck_history(
    conn: sqlite3.Connection, set_name: str, variant_id: str, used_at: str
) -> None:
    conn.execute(
        "INSERT INTO deck_history (set_name, variant_id, used_at) VALUES (?, ?, ?)",
        (set_name, variant_id, used_at),
    )
    conn.commit()


# --- preview state --------------------------------------------------------------


def upsert_preview(
    conn: sqlite3.Connection,
    *,
    morning_date: str,
    status: str,
    day_type: str,
    variant_set: str,
    variant_id: str,
    message_text: str,
    stats_json: str,
    stats_intro: str,
    triggered: bool,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO preview_state
            (morning_date, status, day_type, variant_set, variant_id,
             message_text, stats_json, stats_intro, triggered, created_at,
             resolved_at, resolved_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
        ON CONFLICT (morning_date) DO UPDATE SET
            status = excluded.status,
            day_type = excluded.day_type,
            variant_set = excluded.variant_set,
            variant_id = excluded.variant_id,
            message_text = excluded.message_text,
            stats_json = excluded.stats_json,
            stats_intro = excluded.stats_intro,
            triggered = excluded.triggered,
            created_at = excluded.created_at,
            resolved_at = NULL,
            resolved_by = NULL
        """,
        (
            morning_date,
            status,
            day_type,
            variant_set,
            variant_id,
            message_text,
            stats_json,
            stats_intro,
            int(triggered),
            created_at,
        ),
    )
    conn.execute("DELETE FROM preview_messages WHERE morning_date = ?", (morning_date,))
    conn.commit()


def get_preview(conn: sqlite3.Connection, morning_date: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM preview_state WHERE morning_date = ?", (morning_date,)
    ).fetchone()


def update_preview_content(
    conn: sqlite3.Connection,
    morning_date: str,
    *,
    variant_set: str,
    variant_id: str,
    message_text: str,
) -> None:
    conn.execute(
        """
        UPDATE preview_state
        SET variant_set = ?, variant_id = ?, message_text = ?
        WHERE morning_date = ?
        """,
        (variant_set, variant_id, message_text, morning_date),
    )
    conn.commit()


def resolve_preview(
    conn: sqlite3.Connection,
    morning_date: str,
    status: str,
    resolved_by: int | None,
    resolved_at: str | None,
) -> None:
    conn.execute(
        """
        UPDATE preview_state
        SET status = ?, resolved_by = ?, resolved_at = ?
        WHERE morning_date = ?
        """,
        (status, resolved_by, resolved_at, morning_date),
    )
    conn.commit()


def add_preview_message(
    conn: sqlite3.Connection, morning_date: str, chat_id: int, message_id: int
) -> None:
    conn.execute(
        """
        INSERT INTO preview_messages (morning_date, chat_id, message_id) VALUES (?, ?, ?)
        ON CONFLICT (morning_date, chat_id) DO UPDATE SET message_id = excluded.message_id
        """,
        (morning_date, chat_id, message_id),
    )
    conn.commit()


def preview_messages(conn: sqlite3.Connection, morning_date: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT chat_id, message_id FROM preview_messages WHERE morning_date = ?", (morning_date,)
    ).fetchall()


# --- send log ---------------------------------------------------------------------


def add_send_log(
    conn: sqlite3.Connection,
    *,
    morning_date: str,
    variant_set: str,
    variant_id: str,
    mode: str,
    sent_by: int | None,
    sent_at: str,
    group_message_id: int | None,
) -> None:
    conn.execute(
        """
        INSERT INTO send_log
            (morning_date, variant_set, variant_id, mode, sent_by, sent_at, group_message_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (morning_date, variant_set, variant_id, mode, sent_by, sent_at, group_message_id),
    )
    conn.commit()


# --- custom messages ------------------------------------------------------------


def create_custom_message(
    conn: sqlite3.Connection,
    *,
    text: str,
    scheduled_at: str,
    created_by: int,
    created_at: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO custom_messages
            (text, scheduled_at, status, created_by, created_at, updated_at)
        VALUES (?, ?, 'scheduled', ?, ?, ?)
        """,
        (text, scheduled_at, created_by, created_at, created_at),
    )
    conn.commit()
    return cursor.lastrowid


def get_custom_message(conn: sqlite3.Connection, custom_message_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM custom_messages WHERE id = ?", (custom_message_id,)
    ).fetchone()


def update_custom_message_text(
    conn: sqlite3.Connection, custom_message_id: int, text: str, updated_at: str
) -> None:
    conn.execute(
        "UPDATE custom_messages SET text = ?, updated_at = ? WHERE id = ?",
        (text, updated_at, custom_message_id),
    )
    conn.commit()


def update_custom_message_time(
    conn: sqlite3.Connection, custom_message_id: int, scheduled_at: str, updated_at: str
) -> None:
    conn.execute(
        "UPDATE custom_messages SET scheduled_at = ?, updated_at = ? WHERE id = ?",
        (scheduled_at, updated_at, custom_message_id),
    )
    conn.commit()


def set_custom_message_status(
    conn: sqlite3.Connection, custom_message_id: int, status: str, updated_at: str
) -> None:
    conn.execute(
        "UPDATE custom_messages SET status = ?, updated_at = ? WHERE id = ?",
        (status, updated_at, custom_message_id),
    )
    conn.commit()


def mark_custom_message_sent(
    conn: sqlite3.Connection, custom_message_id: int, sent_at: str
) -> None:
    conn.execute(
        "UPDATE custom_messages SET status = 'sent', sent_at = ?, updated_at = ? WHERE id = ?",
        (sent_at, sent_at, custom_message_id),
    )
    conn.commit()


def due_custom_messages(conn: sqlite3.Connection, now_utc_iso: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM custom_messages WHERE status = 'scheduled' AND scheduled_at <= ?",
        (now_utc_iso,),
    ).fetchall()


def add_custom_message_preview(
    conn: sqlite3.Connection, custom_message_id: int, chat_id: int, message_id: int
) -> None:
    conn.execute(
        """
        INSERT INTO custom_message_previews (custom_message_id, chat_id, message_id)
        VALUES (?, ?, ?)
        ON CONFLICT (custom_message_id, chat_id) DO UPDATE SET message_id = excluded.message_id
        """,
        (custom_message_id, chat_id, message_id),
    )
    conn.commit()


def custom_message_previews(conn: sqlite3.Connection, custom_message_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT chat_id, message_id FROM custom_message_previews WHERE custom_message_id = ?",
        (custom_message_id,),
    ).fetchall()
