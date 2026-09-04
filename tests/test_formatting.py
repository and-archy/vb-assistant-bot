from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from vb_assistant_bot.formatting import format_stats_summary
from vb_assistant_bot.night_logic import AlertWindow, NightStats

_TZ = ZoneInfo("Europe/Kyiv")


def _make_stats(alerts, has_ballistic=False):
    total = sum((a.duration for a in alerts), start=timedelta())
    longest = max(alerts, key=lambda a: a.duration) if alerts else None
    return NightStats(
        morning_date=date(2026, 1, 15),
        window_start=None,
        window_end=None,
        alerts=tuple(alerts),
        total_duration=total,
        longest=longest,
        has_ballistic=has_ballistic,
    )


def _alert(start_h, start_m, end_h, end_m):
    start = datetime(2026, 1, 15, start_h, start_m, tzinfo=_TZ)
    end = datetime(2026, 1, 15, end_h, end_m, tzinfo=_TZ)
    return AlertWindow(
        started_at=start,
        finished_at=end,
        threat_type=None,
        ongoing=False,
        crosses_hard_window=False,
    )


def test_format_stats_summary_no_alerts():
    stats = _make_stats([])
    assert format_stats_summary(stats) == "Статистика ночі: тривог не зафіксовано."


def test_format_stats_summary_pluralization_and_duration():
    stats = _make_stats([_alert(2, 10, 5, 15)])
    text = format_stats_summary(stats)
    assert "1 тривога" in text
    assert "3 год 5 хв" in text
    assert "02:10–05:15" in text


def test_format_stats_summary_plural_few():
    stats = _make_stats([_alert(1, 0, 1, 30), _alert(2, 0, 2, 30), _alert(3, 0, 3, 30)])
    text = format_stats_summary(stats)
    assert "3 тривоги" in text


def test_format_stats_summary_ballistic_note():
    stats = _make_stats([_alert(2, 10, 5, 15)], has_ballistic=True)
    text = format_stats_summary(stats)
    assert "балістики" in text
