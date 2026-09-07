import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from vb_assistant_bot import db
from vb_assistant_bot.content import Thresholds
from vb_assistant_bot.timeutil import parse_api_datetime


@dataclass(frozen=True)
class AlertWindow:
    started_at: datetime
    finished_at: datetime
    threat_types: tuple[str, ...]
    ongoing: bool
    crosses_hard_window: bool

    @property
    def duration(self) -> timedelta:
        return self.finished_at - self.started_at


@dataclass(frozen=True)
class NightStats:
    morning_date: date
    window_start: datetime
    window_end: datetime
    alerts: tuple[AlertWindow, ...]
    total_duration: timedelta
    longest: AlertWindow | None
    has_ballistic: bool

    @property
    def count(self) -> int:
        return len(self.alerts)


def _localize(d: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, t, tzinfo=tz)


def compute_night_stats(
    conn: sqlite3.Connection,
    *,
    location_uid: str,
    morning_date: date,
    timezone: str,
    thresholds: Thresholds,
    now: datetime | None = None,
) -> NightStats:
    tz = ZoneInfo(timezone)
    window_start = _localize(morning_date - timedelta(days=1), thresholds.night_window_start, tz)
    window_end = _localize(morning_date, thresholds.night_window_end, tz)
    hard_start = _localize(morning_date, thresholds.hard_window_start, tz)
    hard_end = _localize(morning_date, thresholds.hard_window_end, tz)
    now = now.astimezone(tz) if now else datetime.now(tz)
    clip_end = min(window_end, now)

    # alerts.started_at/finished_at зберігаються так, як прийшли з API
    # (UTC), тож межі вікна для SQL-порівняння рядків теж треба в UTC —
    # інакше offset у ISO8601 (+02:00 проти +00:00) ламає лексикографічне
    # порівняння рядків у alerts_overlapping.
    utc = ZoneInfo("UTC")
    rows = db.alerts_overlapping(
        conn,
        location_uid,
        window_start.astimezone(utc).isoformat(),
        window_end.astimezone(utc).isoformat(),
    )

    alerts: list[AlertWindow] = []
    total_duration = timedelta()
    longest: AlertWindow | None = None
    has_ballistic = False

    for row in rows:
        raw_start = parse_api_datetime(row["started_at"])
        ongoing = row["finished_at"] is None
        raw_end = parse_api_datetime(row["finished_at"]) if row["finished_at"] else clip_end

        clipped_start = max(raw_start, window_start).astimezone(tz)
        clipped_end = min(raw_end, clip_end).astimezone(tz)
        if clipped_end <= clipped_start:
            continue

        alert = AlertWindow(
            started_at=clipped_start,
            finished_at=clipped_end,
            threat_types=tuple(json.loads(row["threat_types"] or "[]")),
            ongoing=ongoing,
            crosses_hard_window=clipped_start < hard_end and clipped_end > hard_start,
        )
        alerts.append(alert)
        total_duration += alert.duration
        if longest is None or alert.duration > longest.duration:
            longest = alert

        if any(t in thresholds.ballistic_threat_types for t in alert.threat_types):
            has_ballistic = True

    return NightStats(
        morning_date=morning_date,
        window_start=window_start,
        window_end=window_end,
        alerts=tuple(alerts),
        total_duration=total_duration,
        longest=longest,
        has_ballistic=has_ballistic,
    )


def is_heavy_night(stats: NightStats, thresholds: Thresholds) -> bool:
    """ТЗ п.3. Пороги тривалості діляться навпіл, якщо в логах є ознака
    балістики (кількісний поріг '2 і більше' не ділиться — при 1 тривозі
    halving однаково покривається зниженим порогом одиничної тривоги)."""
    multiplier = thresholds.ballistic_threshold_multiplier if stats.has_ballistic else 1.0

    min_total = timedelta(minutes=thresholds.min_total_duration_minutes * multiplier)
    min_single = timedelta(minutes=thresholds.min_single_alert_minutes * multiplier)
    min_hard = timedelta(minutes=thresholds.hard_window_min_duration_minutes * multiplier)

    if stats.count >= thresholds.min_alerts_count and stats.total_duration >= min_total:
        return True

    if stats.longest is not None and stats.longest.duration >= min_single:
        return True

    if any(a.crosses_hard_window and a.duration >= min_hard for a in stats.alerts):
        return True

    return False
