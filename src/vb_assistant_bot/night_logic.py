import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from vb_assistant_bot import db
from vb_assistant_bot.content import Thresholds
from vb_assistant_bot.timeutil import parse_api_datetime

_MISSILE_ALERT_LEVEL = "red"
_DRONE_ALERT_LEVEL = "yellow"


@dataclass(frozen=True)
class AlertWindow:
    started_at: datetime
    finished_at: datetime
    alert_level: str | None  # "red" = ракетна небезпека, "yellow" = дронова (alerts.in.ua)
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

    @property
    def missile_alerts(self) -> tuple[AlertWindow, ...]:
        return tuple(a for a in self.alerts if a.alert_level == _MISSILE_ALERT_LEVEL)

    @property
    def drone_alerts(self) -> tuple[AlertWindow, ...]:
        return tuple(a for a in self.alerts if a.alert_level == _DRONE_ALERT_LEVEL)

    @property
    def missile_duration(self) -> timedelta:
        return _union_duration(self.missile_alerts)

    @property
    def drone_duration(self) -> timedelta:
        return _union_duration(self.drone_alerts)


def _merge_same_level(alerts: list[AlertWindow]) -> list[AlertWindow]:
    """Інцидент 2026-11-02: alerts.in.ua віддав три червоні тривоги, що
    стартували з різницею в 2 хв (дублі однієї події), і бот додав їх
    тривалості — "25 год 20 хв" за 9-годинну ніч. Записи ОДНОГО рівня
    (red/yellow), що перекриваються чи стикаються, зливаються в одну
    тривогу — так кількість, найдовша і розбивка не роздуваються дублями.
    Різні рівні не зливаються: ракетна й дронова — окремі загрози."""
    merged: list[AlertWindow] = []
    for alert in sorted(alerts, key=lambda a: (a.alert_level or "", a.started_at)):
        last = merged[-1] if merged else None
        if (
            last is not None
            and last.alert_level == alert.alert_level
            and alert.started_at <= last.finished_at
        ):
            merged[-1] = replace(
                last,
                finished_at=max(last.finished_at, alert.finished_at),
                threat_types=tuple(dict.fromkeys(last.threat_types + alert.threat_types)),
                ongoing=last.ongoing or alert.ongoing,
            )
        else:
            merged.append(alert)
    merged.sort(key=lambda a: a.started_at)
    return merged


def _union_duration(alerts: list[AlertWindow] | tuple[AlertWindow, ...]) -> timedelta:
    """Час, коли діяла хоча б одна тривога: перекриття (наприклад,
    дронова всередині ракетної) рахується один раз — сума ніколи не
    перевищить вікно ночі."""
    total = timedelta()
    current_start = current_end = None
    for alert in sorted(alerts, key=lambda a: a.started_at):
        if current_end is None or alert.started_at > current_end:
            if current_end is not None:
                total += current_end - current_start
            current_start, current_end = alert.started_at, alert.finished_at
        else:
            current_end = max(current_end, alert.finished_at)
    if current_end is not None:
        total += current_end - current_start
    return total


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

    raw_alerts: list[AlertWindow] = []
    for row in rows:
        raw_start = parse_api_datetime(row["started_at"])
        ongoing = row["finished_at"] is None
        raw_end = parse_api_datetime(row["finished_at"]) if row["finished_at"] else clip_end

        clipped_start = max(raw_start, window_start).astimezone(tz)
        clipped_end = min(raw_end, clip_end).astimezone(tz)
        if clipped_end <= clipped_start:
            continue

        raw_alerts.append(
            AlertWindow(
                started_at=clipped_start,
                finished_at=clipped_end,
                alert_level=row["alert_level"],
                threat_types=tuple(json.loads(row["threat_types"] or "[]")),
                ongoing=ongoing,
                crosses_hard_window=False,
            )
        )

    alerts = [
        replace(a, crosses_hard_window=a.started_at < hard_end and a.finished_at > hard_start)
        for a in _merge_same_level(raw_alerts)
    ]
    longest = max(alerts, key=lambda a: a.duration, default=None)
    total_duration = _union_duration(alerts)
    has_ballistic = any(
        t in thresholds.ballistic_threat_types for a in alerts for t in a.threat_types
    )

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
