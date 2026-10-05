import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

from helpers import make_context

from vb_assistant_bot import alerts_health, db, scheduler


def _ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def _sent_texts(context) -> list[str]:
    return [kwargs["text"] for _, kwargs in context.bot.send_message.await_args_list]


def test_first_failure_only_remembers_start(conn, config):
    context = make_context(conn, config)

    asyncio.run(alerts_health.on_poll_failure(context, RuntimeError("timeout")))

    assert db.get_state(conn, alerts_health.FAILING_SINCE_KEY) is not None
    context.bot.send_message.assert_not_called()


def test_long_outage_notifies_admins_once(conn, config):
    context = make_context(conn, config)
    db.set_state(conn, alerts_health.FAILING_SINCE_KEY, _ago(31))

    asyncio.run(alerts_health.on_poll_failure(context, RuntimeError("timeout")))
    asyncio.run(alerts_health.on_poll_failure(context, RuntimeError("timeout")))

    texts = _sent_texts(context)
    assert len(texts) == len(config.admin_user_ids)
    assert texts[0].startswith("⚠️ alerts.in.ua не відповідає")


def test_recovery_after_notified_outage_sends_message_and_clears(conn, config):
    context = make_context(conn, config)
    db.set_state(conn, alerts_health.FAILING_SINCE_KEY, _ago(40))
    db.set_state(conn, alerts_health.NOTIFIED_KEY, "1")

    asyncio.run(alerts_health.on_poll_success(context))

    texts = _sent_texts(context)
    assert len(texts) == len(config.admin_user_ids)
    assert texts[0].startswith("✅ Дані про тривоги з alerts.in.ua знову оновлюються")
    assert db.get_state(conn, alerts_health.FAILING_SINCE_KEY) is None
    assert db.get_state(conn, alerts_health.NOTIFIED_KEY) is None
    assert db.get_state(conn, alerts_health.LAST_OK_KEY) is not None


def test_short_outage_recovers_silently(conn, config):
    context = make_context(conn, config)
    db.set_state(conn, alerts_health.FAILING_SINCE_KEY, _ago(3))

    asyncio.run(alerts_health.on_poll_success(context))

    context.bot.send_message.assert_not_called()
    assert db.get_state(conn, alerts_health.FAILING_SINCE_KEY) is None


def test_stale_warning(conn):
    assert "жодного разу" in alerts_health.stale_warning(conn, "Europe/Kyiv")
    db.set_state(conn, alerts_health.LAST_OK_KEY, _ago(1))
    assert alerts_health.stale_warning(conn, "Europe/Kyiv") is None
    db.set_state(conn, alerts_health.LAST_OK_KEY, _ago(60))
    assert "можуть бути неповними" in alerts_health.stale_warning(conn, "Europe/Kyiv")


def test_preview_shows_stale_data_warning(conn, config, texts, thresholds):
    db.set_state(conn, alerts_health.LAST_OK_KEY, _ago(60))
    context = make_context(conn, config, texts=texts, thresholds=thresholds)

    asyncio.run(scheduler.generate_and_send_preview(context, force=True))

    intro = db.get_preview(conn, date.today().isoformat())["stats_intro"]
    assert "⚠️ Дані про тривоги можуть бути неповними" in intro


def test_preview_without_warning_when_data_fresh(conn, config, texts, thresholds):
    db.set_state(conn, alerts_health.LAST_OK_KEY, _ago(1))
    context = make_context(conn, config, texts=texts, thresholds=thresholds)

    asyncio.run(scheduler.generate_and_send_preview(context, force=True))

    assert "⚠️ Дані" not in db.get_preview(conn, date.today().isoformat())["stats_intro"]


def test_poll_alerts_records_success_and_failure(conn, config):
    context = make_context(conn, config)
    client = MagicMock()
    client.fetch_region_history = MagicMock(return_value=[])
    context.bot_data["alerts_client"] = client

    asyncio.run(scheduler.poll_alerts(context))
    assert db.get_state(conn, alerts_health.LAST_OK_KEY) is not None

    client.fetch_region_history.side_effect = RuntimeError("down")
    asyncio.run(scheduler.poll_alerts(context))
    assert db.get_state(conn, alerts_health.FAILING_SINCE_KEY) is not None


def test_status_line(conn):
    assert "ще не завантажувались" in alerts_health.status_line(conn, "Europe/Kyiv")
    db.set_state(conn, alerts_health.LAST_OK_KEY, _ago(60))
    db.set_state(conn, alerts_health.FAILING_SINCE_KEY, _ago(59))
    assert "не відповідає" in alerts_health.status_line(conn, "Europe/Kyiv")
