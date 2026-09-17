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


def _format_breakdown_line(stats: NightStats) -> str | None:
    """Розбивка за alert_level (2026-09-18): 'red' = ракетна небезпека,
    'yellow' = дронова — присутнє в кожному записі alerts.in.ua (на
    відміну від threats[], яке на практиці завжди порожнє). Рядок
    з'являється лише якщо є хоч одна категоризована тривога."""
    missile_count = len(stats.missile_alerts)
    drone_count = len(stats.drone_alerts)
    if not missile_count and not drone_count:
        return None

    parts = []
    if missile_count:
        parts.append(
            f"ракетна загроза — {missile_count} {_pluralize_alerts(missile_count)}, "
            f"{_format_duration(stats.missile_duration)}"
        )
    if drone_count:
        parts.append(
            f"дронова загроза — {drone_count} {_pluralize_alerts(drone_count)}, "
            f"{_format_duration(stats.drone_duration)}"
        )
    return "У т.ч.: " + "; ".join(parts) + "."


def format_stats_summary(stats: NightStats, triggered: bool) -> str:
    """triggered — вердикт алгоритму (ТЗ п.3), суто рекомендаційний з
    2026-09-17: прев'ю тепер надсилається щоранку незалежно від нього
    (інцидент 17.09 — масований обстріл не пробив пороги тривалості,
    і бот мовчав; тепер людина завжди бачить статистику й вирішує сама)."""
    if stats.count == 0:
        summary = "Статистика ночі: тривог не зафіксовано."
    else:
        parts = [f"Статистика ночі: {stats.count} {_pluralize_alerts(stats.count)}"]
        parts.append(f"сумарно {_format_duration(stats.total_duration)}")
        if stats.longest is not None:
            start = stats.longest.started_at.strftime("%H:%M")
            end = stats.longest.finished_at.strftime("%H:%M")
            parts.append(f"з них найдовша {start}–{end}")
        summary = ", ".join(parts) + "."
        if stats.has_ballistic:
            summary += " Зафіксовано загрозу балістики."
        breakdown = _format_breakdown_line(stats)
        if breakdown:
            summary += f"\n{breakdown}"

    verdict = (
        "Алгоритм: ніч визнана важкою."
        if triggered
        else "Алгоритм: ніч НЕ визнана важкою — рішення надсилати чи ні за вами."
    )
    return f"{summary}\n{verdict}"
