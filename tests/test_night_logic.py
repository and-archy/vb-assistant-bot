from datetime import date

from vb_assistant_bot import db
from vb_assistant_bot.night_logic import compute_night_stats, is_heavy_night, match_threat_type

_MORNING = date(2026, 1, 15)  # Kyiv EET (UTC+2) — без DST-плутанини
_TZ = "Europe/Kyiv"


def _add_alert(conn, ext_id, start_utc, end_utc, threat_type=None):
    db.upsert_alert(
        conn,
        external_id=ext_id,
        location_uid="31",
        raw_alert_type="air_raid",
        threat_type=threat_type,
        started_at=start_utc,
        finished_at=end_utc,
        updated_at=end_utc or start_utc,
    )


def _stats(conn, thresholds, morning_date=_MORNING):
    return compute_night_stats(
        conn,
        location_uid="31",
        morning_date=morning_date,
        timezone=_TZ,
        thresholds=thresholds,
    )


def test_two_alerts_totaling_two_hours_triggers(conn, thresholds):
    _add_alert(conn, "e1", "2026-01-14T21:00:00+00:00", "2026-01-14T22:00:00+00:00")
    _add_alert(conn, "e2", "2026-01-14T23:00:00+00:00", "2026-01-15T00:00:00+00:00")
    stats = _stats(conn, thresholds)
    assert stats.count == 2
    assert is_heavy_night(stats, thresholds) is True


def test_single_two_hour_alert_triggers(conn, thresholds):
    _add_alert(conn, "e1", "2026-01-14T21:00:00+00:00", "2026-01-14T23:05:00+00:00")
    stats = _stats(conn, thresholds)
    assert stats.count == 1
    assert is_heavy_night(stats, thresholds) is True


def test_hard_window_hour_long_alert_triggers(conn, thresholds):
    # 01:00-05:00 Kyiv = 23:00-03:00 UTC. Тривога 00:00-01:30 UTC (90 хв)
    # перетинає жорсткий проміжок і триває понад годину.
    _add_alert(conn, "e1", "2026-01-15T00:00:00+00:00", "2026-01-15T01:30:00+00:00")
    stats = _stats(conn, thresholds)
    assert is_heavy_night(stats, thresholds) is True


def test_short_alert_before_0030_does_not_trigger(conn, thresholds):
    _add_alert(conn, "e1", "2026-01-14T20:00:00+00:00", "2026-01-14T20:20:00+00:00")
    stats = _stats(conn, thresholds)
    assert stats.count == 1
    assert is_heavy_night(stats, thresholds) is False


def test_alerts_after_0700_are_excluded(conn, thresholds):
    _add_alert(conn, "e1", "2026-01-15T06:00:00+00:00", "2026-01-15T07:00:00+00:00")
    stats = _stats(conn, thresholds)
    assert stats.count == 0
    assert is_heavy_night(stats, thresholds) is False


def test_no_alerts_does_not_trigger(conn, thresholds):
    stats = _stats(conn, thresholds)
    assert is_heavy_night(stats, thresholds) is False


def test_ballistic_halves_single_alert_threshold(conn, thresholds):
    _add_alert(
        conn,
        "e1",
        "2026-01-14T21:00:00+00:00",
        "2026-01-14T22:00:00+00:00",
        threat_type="ballistic",
    )
    stats = _stats(conn, thresholds)
    assert stats.has_ballistic is True
    assert is_heavy_night(stats, thresholds) is True


def test_same_duration_without_ballistic_does_not_trigger(conn, thresholds):
    _add_alert(conn, "e1", "2026-01-14T21:00:00+00:00", "2026-01-14T22:00:00+00:00")
    stats = _stats(conn, thresholds)
    assert stats.has_ballistic is False
    assert is_heavy_night(stats, thresholds) is False


def test_ongoing_alert_is_clipped_to_now(conn, thresholds):
    from datetime import UTC, datetime

    _add_alert(conn, "e1", "2026-01-14T21:00:00+00:00", None)
    now = datetime(2026, 1, 14, 21, 30, tzinfo=UTC)
    stats = compute_night_stats(
        conn,
        location_uid="31",
        morning_date=_MORNING,
        timezone=_TZ,
        thresholds=thresholds,
        now=now,
    )
    assert stats.count == 1
    assert stats.longest.duration.total_seconds() == 30 * 60


def test_match_threat_type_keywords(thresholds):
    assert (
        match_threat_type("Загроза балістичної зброї", thresholds.threat_type_keywords)
        == "ballistic"
    )
    assert match_threat_type("shahed drones spotted", thresholds.threat_type_keywords) == "uav"
    assert match_threat_type("air_raid", thresholds.threat_type_keywords) is None
    assert match_threat_type(None, thresholds.threat_type_keywords) is None
