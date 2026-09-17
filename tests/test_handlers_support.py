import asyncio
from datetime import date
from unittest.mock import patch

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


def test_support_replies_with_confirmation(conn, config, texts, thresholds):
    update = make_update(user_id=111)
    context = make_context(conn, config, texts=texts, thresholds=thresholds)

    asyncio.run(support.support(update, context))

    update.effective_message.reply_text.assert_awaited_once()
    text = update.effective_message.reply_text.await_args.args[0]
    assert f"{len(config.admin_user_ids)}/{len(config.admin_user_ids)}" in text


def test_support_denies_non_admin(conn, config, texts, thresholds):
    update = make_update(user_id=999)
    context = make_context(conn, config, texts=texts, thresholds=thresholds)

    asyncio.run(support.support(update, context))

    assert db.get_preview(conn, date.today().isoformat()) is None
    update.effective_message.reply_text.assert_awaited_once_with("Немає доступу.")


def test_support_replies_on_internal_error_instead_of_silence(conn, config, texts, thresholds):
    """Інцидент 2026-09-17: /support мовчав при внутрішньому збої. Тепер
    завжди відповідає, навіть при помилці."""
    update = make_update(user_id=111)
    context = make_context(conn, config, texts=texts, thresholds=thresholds)

    with patch(
        "vb_assistant_bot.scheduler.generate_and_send_preview", side_effect=RuntimeError("boom")
    ):
        asyncio.run(support.support(update, context))

    update.effective_message.reply_text.assert_awaited_once()
    text = update.effective_message.reply_text.await_args.args[0]
    assert "не вдалося" in text.lower()
