import asyncio
from datetime import date

from helpers import make_context, make_update

from vb_assistant_bot import db
from vb_assistant_bot.handlers import support


def test_support_forces_preview_even_without_trigger(conn, config, texts, thresholds):
    update = make_update(user_id=111)
    context = make_context(conn, config, texts=texts, thresholds=thresholds)

    asyncio.run(support.support(update, context))

    preview = db.get_preview(conn, date.today().isoformat())
    assert preview is not None
    assert preview["status"] == "pending"


def test_support_denies_non_admin(conn, config, texts, thresholds):
    update = make_update(user_id=999)
    context = make_context(conn, config, texts=texts, thresholds=thresholds)

    asyncio.run(support.support(update, context))

    assert db.get_preview(conn, date.today().isoformat()) is None
    update.effective_message.reply_text.assert_awaited_once_with("Немає доступу.")
