import json
import sqlite3

from vb_assistant_bot import db


def test_init_db_migrates_legacy_schema_without_new_columns(tmp_path):
    """Регрес 2026-09-18: сервер мав alerts/preview_state зі старої схеми
    (до threat_types/triggered), і init_db падав з
    'table alerts has no column named threat_types'."""
    path = str(tmp_path / "legacy.db")
    legacy = sqlite3.connect(path)
    legacy.execute(
        """CREATE TABLE alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, external_id TEXT NOT NULL UNIQUE,
            location_uid TEXT NOT NULL, raw_alert_type TEXT, threat_type TEXT,
            started_at TEXT NOT NULL, finished_at TEXT, updated_at TEXT NOT NULL
        )"""
    )
    legacy.execute(
        """CREATE TABLE preview_state (
            morning_date TEXT PRIMARY KEY, status TEXT NOT NULL, day_type TEXT NOT NULL,
            variant_set TEXT NOT NULL, variant_id TEXT NOT NULL, message_text TEXT NOT NULL,
            stats_json TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT,
            resolved_by INTEGER
        )"""
    )
    legacy.commit()
    legacy.close()

    conn = db.init_db(path)
    db.upsert_alert(
        conn,
        external_id="e1",
        location_uid="31",
        raw_alert_type="air_raid",
        alert_level="yellow",
        threat_types=["drones"],
        started_at="2026-09-18T02:00:00+00:00",
        finished_at=None,
        updated_at="2026-09-18T02:00:00+00:00",
    )
    row = conn.execute("SELECT * FROM alerts WHERE external_id = 'e1'").fetchone()
    assert json.loads(row["threat_types"]) == ["drones"]
    assert row["alert_level"] == "yellow"


def test_upsert_alert_inserts_and_updates(conn):
    db.upsert_alert(
        conn,
        external_id="ext-1",
        location_uid="31",
        raw_alert_type="air_raid",
        alert_level=None,
        threat_types=[],
        started_at="2026-09-03T20:00:00+00:00",
        finished_at=None,
        updated_at="2026-09-03T20:00:00+00:00",
    )
    rows = db.alerts_overlapping(
        conn, "31", "2026-09-03T00:00:00+00:00", "2026-09-04T23:59:59+00:00"
    )
    assert len(rows) == 1
    assert rows[0]["finished_at"] is None

    db.upsert_alert(
        conn,
        external_id="ext-1",
        location_uid="31",
        raw_alert_type="air_raid",
        alert_level="red",
        threat_types=["drones"],
        started_at="2026-09-03T20:00:00+00:00",
        finished_at="2026-09-03T21:00:00+00:00",
        updated_at="2026-09-03T21:00:00+00:00",
    )
    rows = db.alerts_overlapping(
        conn, "31", "2026-09-03T00:00:00+00:00", "2026-09-04T23:59:59+00:00"
    )
    assert len(rows) == 1
    assert rows[0]["finished_at"] == "2026-09-03T21:00:00+00:00"
    assert json.loads(rows[0]["threat_types"]) == ["drones"]
    assert rows[0]["alert_level"] == "red"


def test_alerts_overlapping_excludes_outside_window(conn):
    db.upsert_alert(
        conn,
        external_id="ext-2",
        location_uid="31",
        raw_alert_type="air_raid",
        alert_level=None,
        threat_types=[],
        started_at="2026-09-01T10:00:00+00:00",
        finished_at="2026-09-01T10:30:00+00:00",
        updated_at="2026-09-01T10:30:00+00:00",
    )
    rows = db.alerts_overlapping(
        conn, "31", "2026-09-03T00:00:00+00:00", "2026-09-04T23:59:59+00:00"
    )
    assert rows == []


def test_alerts_overlapping_filters_by_location(conn):
    db.upsert_alert(
        conn,
        external_id="ext-3",
        location_uid="30",
        raw_alert_type="air_raid",
        alert_level=None,
        threat_types=[],
        started_at="2026-09-03T20:00:00+00:00",
        finished_at="2026-09-03T21:00:00+00:00",
        updated_at="2026-09-03T21:00:00+00:00",
    )
    rows = db.alerts_overlapping(
        conn, "31", "2026-09-03T00:00:00+00:00", "2026-09-04T23:59:59+00:00"
    )
    assert rows == []


def test_manual_day_type_set_get_clear(conn):
    assert db.get_manual_day_type(conn, "2026-12-25") is None
    db.set_manual_day_type(conn, "2026-12-25", "weekend", 111, "2026-09-01T00:00:00+00:00")
    assert db.get_manual_day_type(conn, "2026-12-25") == "weekend"

    db.set_manual_day_type(conn, "2026-12-25", "workday", 111, "2026-09-02T00:00:00+00:00")
    assert db.get_manual_day_type(conn, "2026-12-25") == "workday"

    db.clear_manual_day_type(conn, "2026-12-25")
    assert db.get_manual_day_type(conn, "2026-12-25") is None


def test_deck_state_roundtrip(conn):
    assert db.get_deck_remaining(conn, "A") is None
    db.save_deck_remaining(conn, "A", ["A1", "A2", "A3"])
    assert db.get_deck_remaining(conn, "A") == ["A1", "A2", "A3"]
    db.add_deck_history(conn, "A", "A1", "2026-09-04T07:01:00+00:00")


def test_preview_lifecycle(conn):
    db.upsert_preview(
        conn,
        morning_date="2026-09-04",
        status="pending",
        day_type="workday",
        variant_set="A",
        variant_id="A1",
        message_text="текст",
        stats_json="{}",
        stats_intro="Статистика ночі: тривог не зафіксовано.",
        triggered=True,
        created_at="2026-09-04T07:01:00+00:00",
    )
    preview = db.get_preview(conn, "2026-09-04")
    assert preview["status"] == "pending"
    assert preview["variant_id"] == "A1"
    assert preview["triggered"] == 1

    db.add_preview_message(conn, "2026-09-04", 111, 999)
    db.add_preview_message(conn, "2026-09-04", 222, 1000)
    messages = db.preview_messages(conn, "2026-09-04")
    assert {row["chat_id"] for row in messages} == {111, 222}

    db.update_preview_content(
        conn, "2026-09-04", variant_set="B", variant_id="B1", message_text="інший текст"
    )
    preview = db.get_preview(conn, "2026-09-04")
    assert preview["variant_set"] == "B"
    assert preview["message_text"] == "інший текст"

    db.resolve_preview(conn, "2026-09-04", "sent", 111, "2026-09-04T07:05:00+00:00")
    preview = db.get_preview(conn, "2026-09-04")
    assert preview["status"] == "sent"
    assert preview["resolved_by"] == 111


def test_resolve_preview_queued_sets_and_clears_scheduled_at(conn):
    db.upsert_preview(
        conn,
        morning_date="2026-09-04",
        status="pending",
        day_type="workday",
        variant_set="A",
        variant_id="A1",
        message_text="текст",
        stats_json="{}",
        stats_intro="",
        triggered=False,
        created_at="2026-09-04T07:01:00+00:00",
    )
    db.resolve_preview(
        conn,
        "2026-09-04",
        "queued",
        111,
        "2026-09-04T07:05:00+00:00",
        scheduled_at="2026-09-04T07:30:00+00:00",
    )
    assert db.get_preview(conn, "2026-09-04")["scheduled_at"] == "2026-09-04T07:30:00+00:00"

    # Скасування (повернення до "pending") прибирає й заплановий час.
    db.resolve_preview(conn, "2026-09-04", "pending", None, None)
    assert db.get_preview(conn, "2026-09-04")["scheduled_at"] is None


def test_due_queued_previews_filters_by_scheduled_at(conn):
    db.upsert_preview(
        conn,
        morning_date="2026-09-04",
        status="pending",
        day_type="workday",
        variant_set="A",
        variant_id="A1",
        message_text="текст",
        stats_json="{}",
        stats_intro="",
        triggered=False,
        created_at="2026-09-04T07:01:00+00:00",
    )
    db.resolve_preview(
        conn,
        "2026-09-04",
        "queued",
        111,
        "2026-09-04T07:05:00+00:00",
        scheduled_at="2026-09-04T07:30:00+00:00",
    )

    assert db.due_queued_previews(conn, "2026-09-04T07:00:00+00:00") == []
    due = db.due_queued_previews(conn, "2026-09-04T08:00:00+00:00")
    assert len(due) == 1
    assert due[0]["morning_date"] == "2026-09-04"


def test_upsert_preview_resets_resolution_and_messages(conn):
    db.upsert_preview(
        conn,
        morning_date="2026-09-04",
        status="pending",
        day_type="workday",
        variant_set="A",
        variant_id="A1",
        message_text="текст",
        stats_json="{}",
        stats_intro="Статистика ночі: тривог не зафіксовано.",
        triggered=True,
        created_at="2026-09-04T07:01:00+00:00",
    )
    db.add_preview_message(conn, "2026-09-04", 111, 999)
    db.resolve_preview(conn, "2026-09-04", "sent", 111, "2026-09-04T07:05:00+00:00")

    # /support перезапускає прев'ю на ту саму дату (наприклад після "sent").
    db.upsert_preview(
        conn,
        morning_date="2026-09-04",
        status="pending",
        day_type="workday",
        variant_set="A",
        variant_id="A2",
        message_text="новий текст",
        stats_json="{}",
        stats_intro="Статистика ночі: тривог не зафіксовано.",
        triggered=True,
        created_at="2026-09-04T09:00:00+00:00",
    )
    preview = db.get_preview(conn, "2026-09-04")
    assert preview["status"] == "pending"
    assert preview["resolved_by"] is None
    assert preview["scheduled_at"] is None
    assert db.preview_messages(conn, "2026-09-04") == []


def test_send_log_insert(conn):
    db.add_send_log(
        conn,
        morning_date="2026-09-04",
        variant_set="A",
        variant_id="A1",
        mode="manual",
        sent_by=111,
        sent_at="2026-09-04T07:05:00+00:00",
        group_message_id=42,
    )
    row = conn.execute("SELECT * FROM send_log").fetchone()
    assert row["mode"] == "manual"
    assert row["sent_by"] == 111
