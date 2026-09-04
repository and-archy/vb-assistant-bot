import random
import sqlite3
from datetime import UTC, datetime

from vb_assistant_bot import db
from vb_assistant_bot.content import Variant


def draw_next(conn: sqlite3.Connection, set_name: str, variants: tuple[Variant, ...]) -> Variant:
    """ТЗ п.11: 'перетасована колода' — проходимо всі варіанти набору у
    довільному порядку, тасуємо заново лише коли всі вичерпані."""
    all_ids = [v.id for v in variants]
    remaining = db.get_deck_remaining(conn, set_name)
    if not remaining or not set(remaining) <= set(all_ids):
        remaining = all_ids.copy()
        random.shuffle(remaining)

    variant_id = remaining.pop(0)
    if not remaining:
        remaining = all_ids.copy()
        random.shuffle(remaining)

    db.save_deck_remaining(conn, set_name, remaining)
    db.add_deck_history(conn, set_name, variant_id, datetime.now(UTC).isoformat())

    by_id = {v.id: v for v in variants}
    return by_id[variant_id]
