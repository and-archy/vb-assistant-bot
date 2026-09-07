import json

from vb_assistant_bot import db


def test_upsert_alert_inserts_and_updates(conn):
    db.upsert_alert(
        conn,
        external_id="ext-1",
        location_uid="31",
        raw_alert_type="air_raid",
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


def test_alerts_overlapping_excludes_outside_window(conn):
    db.upsert_alert(
        conn,
        external_id="ext-2",
        location_uid="31",
        raw_alert_type="air_raid",
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
        created_at="2026-09-04T07:01:00+00:00",
    )
    preview = db.get_preview(conn, "2026-09-04")
    assert preview["status"] == "pending"
    assert preview["variant_id"] == "A1"

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
        created_at="2026-09-04T09:00:00+00:00",
    )
    preview = db.get_preview(conn, "2026-09-04")
    assert preview["status"] == "pending"
    assert preview["resolved_by"] is None
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
