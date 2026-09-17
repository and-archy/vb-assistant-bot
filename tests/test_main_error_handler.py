import asyncio

from helpers import make_context

from vb_assistant_bot.__main__ import on_error


def test_on_error_notifies_all_admins(conn, config):
    context = make_context(conn, config)
    context.error = RuntimeError("щось зламалось")

    asyncio.run(on_error(object(), context))

    assert context.bot.send_message.await_count == len(config.admin_user_ids)
    _, kwargs = context.bot.send_message.await_args
    assert "щось зламалось" in kwargs["text"]


def test_on_error_noop_without_config(conn):
    context = make_context(conn, config=None)
    context.bot_data = {}
    context.error = RuntimeError("боно")

    asyncio.run(on_error(object(), context))

    context.bot.send_message.assert_not_called()
