import asyncio

from helpers import make_context, make_update

from vb_assistant_bot import db
from vb_assistant_bot.handlers import weekend


def test_mark_weekend_sets_manual_day_type(conn, config):
    update = make_update(user_id=111)
    context = make_context(conn, config, args=["25.12.2026"])

    asyncio.run(weekend.mark_weekend(update, context))

    assert db.get_manual_day_type(conn, "2026-12-25") == "weekend"
    update.effective_message.reply_text.assert_awaited_once()


def test_mark_workday_sets_manual_day_type(conn, config):
    update = make_update(user_id=111)
    context = make_context(conn, config, args=["25.12.2026"])
    asyncio.run(weekend.mark_workday(update, context))
    assert db.get_manual_day_type(conn, "2026-12-25") == "workday"


def test_mark_weekend_defaults_to_today(conn, config):
    from datetime import date

    update = make_update(user_id=111)
    context = make_context(conn, config, args=[])
    asyncio.run(weekend.mark_weekend(update, context))
    assert db.get_manual_day_type(conn, date.today().isoformat()) == "weekend"


def test_mark_weekend_rejects_bad_date_format(conn, config):
    update = make_update(user_id=111)
    context = make_context(conn, config, args=["2026-12-25"])
    asyncio.run(weekend.mark_weekend(update, context))
    update.effective_message.reply_text.assert_awaited_once()
    assert "дд.мм.рррр" in update.effective_message.reply_text.await_args.args[0]


def test_mark_weekend_denies_non_admin(conn, config):
    update = make_update(user_id=999)
    context = make_context(conn, config, args=["25.12.2026"])
    asyncio.run(weekend.mark_weekend(update, context))
    assert db.get_manual_day_type(conn, "2026-12-25") is None
    update.effective_message.reply_text.assert_awaited_once_with("Немає доступу.")
