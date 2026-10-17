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


def _age_series(bot_data, first_ago: int, last_ago: int) -> None:
    """Зсуває вже записану серію в минуле (first/last — секунди тому)."""
    state = bot_data["error_state"]
    state["first"] = _old(first_ago)
    state["last"] = _old(last_ago)


def test_nightly_network_blip_is_silent(conn, config):
    """Сценарій 4:10: дві невдалі спроби getUpdates за кілька секунд."""
    context = make_context(conn, config)
    context.error = NetworkError("httpx.ConnectError: ")
    asyncio.run(on_error(None, context))
    asyncio.run(on_error(None, context))
    _age_series(context.bot_data, health.RECOVERY_QUIET_SECONDS + 5, health.RECOVERY_QUIET_SECONDS)

    asyncio.run(health.job_check_recovery(context))

    context.bot.send_message.assert_not_called()
    assert "error_state" not in context.bot_data


def test_network_error_from_polling_does_not_try_to_alert(conn, config):
    context = make_context(conn, config)
    context.error = NetworkError("httpx.ReadError: ")

    asyncio.run(on_error(None, context))

    context.bot.send_message.assert_not_called()
    assert context.bot_data["error_state"]["network_only"] is True


def test_long_network_outage_reported_once_after_recovery(conn, config):
    context = make_context(conn, config)
    context.error = NetworkError("httpx.ConnectError: ")
    asyncio.run(on_error(None, context))
    asyncio.run(on_error(None, context))
    _age_series(
        context.bot_data,
        health.NETWORK_SILENT_SECONDS + health.RECOVERY_QUIET_SECONDS + 60,
        health.RECOVERY_QUIET_SECONDS + 1,
    )

    asyncio.run(health.job_check_recovery(context))

    assert context.bot.send_message.await_count == len(config.admin_user_ids)
    text = context.bot.send_message.await_args.kwargs["text"]
    assert text.startswith("⚠️ Не було зв'язку з Telegram з ")
    assert "2 невдалих спроб" in text
    assert "error_state" not in context.bot_data


def test_network_error_with_update_is_a_real_error(conn, config):
    context = make_context(conn, config)
    context.error = NetworkError("send failed")

    asyncio.run(on_error(object(), context))

    assert context.bot.send_message.await_count == len(config.admin_user_ids)
    assert context.bot_data["error_state"]["network_only"] is False


def test_real_error_after_blip_is_not_network_only(conn, config):
    context = make_context(conn, config)
    context.error = NetworkError("httpx.ReadError: ")
    asyncio.run(on_error(None, context))
    context.error = RuntimeError("щось зламалось")
    asyncio.run(on_error(object(), context))

    assert context.bot_data["error_state"]["network_only"] is False


def test_recovery_includes_error_if_alert_was_not_delivered(conn, config):
    context = make_context(conn, config)
    context.error = RuntimeError("щось зламалось")
    context.bot.send_message.side_effect = NetworkError("down")
    asyncio.run(on_error(object(), context))
    context.bot.send_message.side_effect = None
    _age_series(context.bot_data, health.RECOVERY_QUIET_SECONDS + 5, health.RECOVERY_QUIET_SECONDS)

    asyncio.run(health.job_check_recovery(context))

    text = context.bot.send_message.await_args.kwargs["text"]
    assert text.startswith("✅ Бот знову працює")
    assert "Помилка: щось зламалось" in text


def test_recovery_omits_error_if_alert_was_delivered(conn, config):
    context = make_context(conn, config)
    context.error = RuntimeError("щось зламалось")
    asyncio.run(on_error(object(), context))
    _age_series(context.bot_data, health.RECOVERY_QUIET_SECONDS + 5, health.RECOVERY_QUIET_SECONDS)

    asyncio.run(health.job_check_recovery(context))

    assert "Помилка:" not in context.bot.send_message.await_args.kwargs["text"]
