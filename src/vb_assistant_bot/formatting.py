from datetime import timedelta

from vb_assistant_bot.night_logic import NightStats


def _pluralize_alerts(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "тривога"
    if 2 <= count % 10 <= 4 and not (12 <= count % 100 <= 14):
        return "тривоги"
    return "тривог"


def _format_duration(delta: timedelta) -> str:
    total_minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} год {minutes} хв"
    if hours:
        return f"{hours} год"
    return f"{minutes} хв"


def format_stats_summary(stats: NightStats) -> str:
    if stats.count == 0:
        return "Статистика ночі: тривог не зафіксовано."

    parts = [f"Статистика ночі: {stats.count} {_pluralize_alerts(stats.count)}"]
    parts.append(f"сумарно {_format_duration(stats.total_duration)}")
    if stats.longest is not None:
        start = stats.longest.started_at.strftime("%H:%M")
        end = stats.longest.finished_at.strftime("%H:%M")
        parts.append(f"з них найдовша {start}–{end}")
    summary = ", ".join(parts) + "."
    if stats.has_ballistic:
        summary += " Зафіксовано загрозу балістики."
    return summary
