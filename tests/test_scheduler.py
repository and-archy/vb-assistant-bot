import asyncio
from dataclasses import replace
from datetime import date

from helpers import make_context, make_update

from vb_assistant_bot import db, scheduler


def test_determine_day_type_weekday(conn):
    assert scheduler.determine_day_type(conn, date(2026, 9, 3)) == "workday"  # четвер


def test_determine_day_type_weekend(conn):
    assert scheduler.determine_day_type(conn, date(2026, 9, 5)) == "weekend"  # субота


def test_determine_day_type_manual_override(conn):
    db.set_manual_day_type(conn, "2026-12-25", "weekend", 111, "2026-01-01T00:00:00+00:00")
    assert scheduler.determine_day_type(conn, date(2026, 12, 25)) == "weekend"


def test_generate_and_send_preview_force_sends_to_all_admins(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)

    result = asyncio.run(scheduler.generate_and_send_preview(context, force=True))

    assert result.generated is True
    assert result.sent_to == len(config.admin_user_ids)
    assert context.bot.send_message.await_count == len(config.admin_user_ids)
    morning_key = date.today().isoformat()
    preview = db.get_preview(conn, morning_key)
    assert preview is not None
    assert preview["status"] == "pending"
    assert len(db.preview_messages(conn, morning_key)) == len(config.admin_user_ids)


def test_generate_and_send_preview_sends_even_without_trigger(conn, config, texts, thresholds):
    """Інцидент 2026-09-17: масований обстріл не пробив пороги, і щоденна
    джоба (force=False) нічого не надіслала. Тепер прев'ю йде завжди,
    тригер лише позначка в тексті."""
    context = make_context(conn, config, texts=texts, thresholds=thresholds)

    result = asyncio.run(scheduler.generate_and_send_preview(context, force=False))

    assert result.generated is True
    assert result.triggered is False
    assert result.sent_to == len(config.admin_user_ids)
    context.bot.send_message.assert_called()


def test_generate_and_send_preview_skips_if_already_resolved_today(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    db.resolve_preview(conn, morning_key, "sent", 111, "2026-01-01T00:00:00+00:00")
    context.bot.send_message.reset_mock()

    result = asyncio.run(scheduler.generate_and_send_preview(context, force=False))

    assert result.generated is False
    context.bot.send_message.assert_not_called()


def test_generate_and_send_preview_force_regenerates_even_if_resolved(
    conn, config, texts, thresholds
):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    db.resolve_preview(conn, morning_key, "sent", 111, "2026-01-01T00:00:00+00:00")
    context.bot.send_message.reset_mock()

    result = asyncio.run(scheduler.generate_and_send_preview(context, force=True))

    assert result.generated is True
    assert db.get_preview(conn, morning_key)["status"] == "pending"


def test_on_preview_action_send_publishes_and_resolves(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    context.bot.send_message.reset_mock()

    update = make_update(user_id=111, callback_data=f"prev:{morning_key}:send")
    asyncio.run(scheduler.on_preview_action(update, context))

    preview = db.get_preview(conn, morning_key)
    assert preview["status"] == "sent"
    assert preview["resolved_by"] == 111
    context.bot.send_message.assert_awaited_once()
    _, kwargs = context.bot.send_message.await_args
    assert kwargs["chat_id"] == config.group_chat_id
    row = conn.execute("SELECT * FROM send_log").fetchone()
    assert row["mode"] == "manual"


def test_on_preview_action_skip_resolves_without_publishing(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    context.bot.send_message.reset_mock()

    update = make_update(user_id=111, callback_data=f"prev:{morning_key}:skip")
    asyncio.run(scheduler.on_preview_action(update, context))

    preview = db.get_preview(conn, morning_key)
    assert preview["status"] == "skipped"
    context.bot.send_message.assert_not_called()


def test_on_preview_action_calm_switches_to_set_b(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()

    update = make_update(user_id=111, callback_data=f"prev:{morning_key}:calm")
    asyncio.run(scheduler.on_preview_action(update, context))

    preview = db.get_preview(conn, morning_key)
    assert preview["variant_set"] == "B"
    assert preview["status"] == "pending"
    update.callback_query.edit_message_text.assert_awaited_once()


def test_on_preview_action_more_draws_another_variant_same_set(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    original_variant_id = db.get_preview(conn, morning_key)["variant_id"]

    update = make_update(user_id=111, callback_data=f"prev:{morning_key}:more")
    asyncio.run(scheduler.on_preview_action(update, context))

    preview = db.get_preview(conn, morning_key)
    assert preview["variant_set"] in ("A", "V")
    # Колода без повторів, поки не вичерпана (ТЗ п.11) — набори мають
    # щонайменше 3 варіанти, тож другий draw гарантовано інший id.
    assert preview["variant_id"] != original_variant_id


def test_on_preview_action_denies_non_admin(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()

    update = make_update(user_id=999, callback_data=f"prev:{morning_key}:send")
    asyncio.run(scheduler.on_preview_action(update, context))

    preview = db.get_preview(conn, morning_key)
    assert preview["status"] == "pending"
    update.callback_query.answer.assert_awaited_once_with("Немає доступу", show_alert=True)


def test_on_preview_action_already_resolved_shows_notice(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    db.resolve_preview(conn, morning_key, "sent", 111, "2026-01-01T00:00:00+00:00")

    update = make_update(user_id=222, callback_data=f"prev:{morning_key}:send")
    asyncio.run(scheduler.on_preview_action(update, context))

    update.callback_query.answer.assert_awaited_once_with("Вже оброблено")


def _force_triggered(conn, morning_key: str) -> None:
    conn.execute("UPDATE preview_state SET triggered = 1 WHERE morning_date = ?", (morning_key,))
    conn.commit()


def test_job_autopublish_noop_when_not_triggered(conn, config, texts, thresholds):
    """Не важка ніч (дефолт без тривог у тестовій БД) — навіть якщо
    прев'ю pending, о 08:00 не публікує й не нагадує (ТЗ п.4: тиша по
    спокійній ночі — це правильний результат, не збій)."""
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    context.bot.send_message.reset_mock()

    asyncio.run(scheduler.job_autopublish(context))

    context.bot.send_message.assert_not_called()


def test_job_autopublish_sends_reminder_when_disabled(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    _force_triggered(conn, morning_key)
    context.bot.send_message.reset_mock()

    asyncio.run(scheduler.job_autopublish(context))

    preview = db.get_preview(conn, morning_key)
    assert preview["status"] == "pending"
    assert context.bot.send_message.await_count == len(config.admin_user_ids)


def test_job_autopublish_publishes_when_enabled(conn, config, texts, thresholds):
    enabled_config = replace(config, auto_publish_enabled=True)
    context = make_context(conn, enabled_config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    _force_triggered(conn, morning_key)
    context.bot.send_message.reset_mock()

    asyncio.run(scheduler.job_autopublish(context))

    preview = db.get_preview(conn, morning_key)
    assert preview["status"] == "auto_sent"
    row = conn.execute("SELECT * FROM send_log").fetchone()
    assert row["mode"] == "auto"


def test_job_autopublish_noop_without_pending_preview(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.job_autopublish(context))
    context.bot.send_message.assert_not_called()
