import asyncio

from helpers import make_context
from telegram.error import Forbidden

from vb_assistant_bot import whats_new
from vb_assistant_bot.handlers.menu import MAIN_KEYBOARD


def test_announces_once_per_admin_with_new_keyboard(conn, config):
    context = make_context(conn, config)

    asyncio.run(whats_new.job_announce(context))
    asyncio.run(whats_new.job_announce(context))

    assert context.bot.send_message.await_count == len(config.admin_user_ids)
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["reply_markup"] is MAIN_KEYBOARD
    assert kwargs["text"].startswith("🆕 Бот оновлено")


def test_retries_admin_who_did_not_receive(conn, config):
    context = make_context(conn, config)
    context.bot.send_message.side_effect = Forbidden("bot was blocked")
    asyncio.run(whats_new.job_announce(context))

    context.bot.send_message.side_effect = None
    context.bot.send_message.reset_mock()
    asyncio.run(whats_new.job_announce(context))

    assert context.bot.send_message.await_count == len(config.admin_user_ids)
