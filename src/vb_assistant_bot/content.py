import json
from dataclasses import dataclass, field
from datetime import time


@dataclass(frozen=True)
class Variant:
    id: str
    text: str


@dataclass(frozen=True)
class Texts:
    weekday_arrangements: str
    contacts: str
    sets: dict[str, tuple[Variant, ...]]


@dataclass(frozen=True)
class Thresholds:
    night_window_start: time
    night_window_end: time
    hard_window_start: time
    hard_window_end: time
    hard_window_min_duration_minutes: int
    min_alerts_count: int
    min_total_duration_minutes: int
    min_single_alert_minutes: int
    ballistic_threshold_multiplier: float
    ballistic_threat_types: frozenset[str]
    threat_type_keywords: dict[str, tuple[str, ...]] = field(default_factory=dict)
    preview_time: time = time(7, 1)
    autopublish_time: time = time(8, 0)
    alerts_poll_interval_seconds: int = 90


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def load_texts(path: str) -> Texts:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)

    sets: dict[str, tuple[Variant, ...]] = {}
    for set_name, items in raw["sets"].items():
        variants = tuple(Variant(id=item["id"], text=item["text"]) for item in items)
        if not variants:
            raise RuntimeError(f"Набір текстів {set_name!r} у {path} порожній")
        sets[set_name] = variants

    return Texts(
        weekday_arrangements=raw["stable_blocks"]["weekday_arrangements"],
        contacts=raw["stable_blocks"]["contacts"],
        sets=sets,
    )


def load_thresholds(path: str) -> Thresholds:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)

    keywords = {
        threat_type: tuple(words)
        for threat_type, words in raw.get("threat_type_keywords", {}).items()
    }

    return Thresholds(
        night_window_start=_parse_hhmm(raw["night_window_start"]),
        night_window_end=_parse_hhmm(raw["night_window_end"]),
        hard_window_start=_parse_hhmm(raw["hard_window_start"]),
        hard_window_end=_parse_hhmm(raw["hard_window_end"]),
        hard_window_min_duration_minutes=int(raw["hard_window_min_duration_minutes"]),
        min_alerts_count=int(raw["min_alerts_count"]),
        min_total_duration_minutes=int(raw["min_total_duration_minutes"]),
        min_single_alert_minutes=int(raw["min_single_alert_minutes"]),
        ballistic_threshold_multiplier=float(raw["ballistic_threshold_multiplier"]),
        ballistic_threat_types=frozenset(raw.get("ballistic_threat_types", ["ballistic"])),
        threat_type_keywords=keywords,
        preview_time=_parse_hhmm(raw.get("preview_time", "07:01")),
        autopublish_time=_parse_hhmm(raw.get("autopublish_time", "08:00")),
        alerts_poll_interval_seconds=int(raw.get("alerts_poll_interval_seconds", 90)),
    )
