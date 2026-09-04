from vb_assistant_bot import db, deck
from vb_assistant_bot.content import Variant

_VARIANTS = (Variant("A1", "text1"), Variant("A2", "text2"), Variant("A3", "text3"))


def test_draw_next_exhausts_all_before_repeat(conn):
    drawn = [deck.draw_next(conn, "A", _VARIANTS).id for _ in range(3)]
    assert set(drawn) == {"A1", "A2", "A3"}


def test_draw_next_reshuffles_after_exhaustion(conn):
    first_pass = [deck.draw_next(conn, "A", _VARIANTS).id for _ in range(3)]
    fourth = deck.draw_next(conn, "A", _VARIANTS).id
    assert fourth in {v.id for v in _VARIANTS}
    assert set(first_pass) == {"A1", "A2", "A3"}


def test_draw_next_persists_across_calls(conn):
    deck.draw_next(conn, "A", _VARIANTS)
    remaining = db.get_deck_remaining(conn, "A")
    assert remaining is not None
    assert len(remaining) == 2


def test_draw_next_records_history(conn):
    variant = deck.draw_next(conn, "A", _VARIANTS)
    history = conn.execute("SELECT * FROM deck_history").fetchall()
    assert len(history) == 1
    assert history[0]["variant_id"] == variant.id


def test_draw_next_reshuffles_when_variant_set_changed(conn):
    db.save_deck_remaining(conn, "A", ["A1", "GONE"])
    variant = deck.draw_next(conn, "A", _VARIANTS)
    assert variant.id in {v.id for v in _VARIANTS}
