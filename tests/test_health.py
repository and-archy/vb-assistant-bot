import asyncio
from datetime import UTC, datetime, timedelta

from helpers import make_context
from telegram.error import NetworkError

from vb_assistant_bot import health
from vb_assistant_bot.__main__ import on_error


def _old(seconds: int) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


def test_on_error_records_error_state(conn, config):
    context = make_context(conn, config)
    context.error = NetworkError("httpx.ReadError: ")

    asyncio.run(on_error(None, context))
    asyncio.run(on_error(None, context))

    assert context.bot_data["error_state"]["count"] == 2


def test_no_recovery_message_without_errors(conn, config):
    context = make_context(conn, config)

    asyncio.run(health.job_check_recovery(context))

    context.bot.send_message.assert_not_called()


def test_no_recovery_message_while_errors_are_recent(conn, config):
    context = make_context(conn, config)
    health.record_error(context.bot_data)

    asyncio.run(health.job_check_recovery(context))

    context.bot.send_message.assert_not_called()
    assert "error_state" in context.bot_data


def test_recovery_message_sent_after_quiet_period(conn, config):
    context = make_context(conn, config)
    health.record_error(context.bot_data, _old(health.RECOVERY_QUIET_SECONDS + 120))
    health.record_error(context.bot_data, _old(health.RECOVERY_QUIET_SECONDS + 60))

    asyncio.run(health.job_check_recovery(context))

    assert context.bot.send_message.await_count == len(config.admin_user_ids)
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["text"].startswith("✅ Бот знову працює в штатному режимі")
    assert "Було помилок: 2" in kwargs["text"]
    assert "error_state" not in context.bot_data


def test_recovery_waits_while_telegram_unreachable(conn, config):
    context = make_context(conn, config)
    health.record_error(context.bot_data, _old(health.RECOVERY_QUIET_SECONDS + 60))
    context.bot.get_me.side_effect = NetworkError("down")

    asyncio.run(health.job_check_recovery(context))

    context.bot.send_message.assert_not_called()
    assert "error_state" in context.bot_data


def test_recovery_state_kept_if_no_admin_got_message(conn, config):
    context = make_context(conn, config)
    health.record_error(context.bot_data, _old(health.RECOVERY_QUIET_SECONDS + 60))
    context.bot.send_message.side_effect = NetworkError("down")

    asyncio.run(health.job_check_recovery(context))

    assert "error_state" in context.bot_data
