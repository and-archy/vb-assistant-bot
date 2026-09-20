import asyncio
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from helpers import make_context, make_update
from telegram.error import NetworkError

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
    db.resolve_preview(conn, morning_key, "skipped", 111, "2026-01-01T00:00:00+00:00")
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
    db.resolve_preview(conn, morning_key, "skipped", 111, "2026-01-01T00:00:00+00:00")
    context.bot.send_message.reset_mock()

    result = asyncio.run(scheduler.generate_and_send_preview(context, force=True))

    assert result.generated is True
    assert db.get_preview(conn, morning_key)["status"] == "pending"


def test_on_preview_action_send_shows_time_choice_without_resolving(
    conn, config, texts, thresholds
):
    """'Надіслати' більше не планує миттєво — спершу питає, коли
    публікувати: Зараз / фіксований час / обрати інший (вимога
    2026-09-20)."""
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    context.bot.send_message.reset_mock()

    update = make_update(user_id=111, callback_data=f"prev:{morning_key}:send")
    asyncio.run(scheduler.on_preview_action(update, context))

    preview = db.get_preview(conn, morning_key)
    assert preview["status"] == "pending"  # ще нічого не обрано
    context.bot.send_message.assert_not_called()
    update.callback_query.edit_message_text.assert_awaited_once()
    body, kwargs = (
        update.callback_query.edit_message_text.await_args.args[0],
        (update.callback_query.edit_message_text.await_args.kwargs),
    )
    assert "Коли відправити?" in body
    labels = [button.text for row in kwargs["reply_markup"].inline_keyboard for button in row]
    assert "Зараз" in labels
    assert any("Відправити" in label for label in labels)
    assert "Обрати інший час відправки" in labels
    assert "Назад" in labels


def test_on_preview_action_send_back_returns_to_pending_view(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()

    update = make_update(user_id=111, callback_data=f"prev:{morning_key}:send_back")
    asyncio.run(scheduler.on_preview_action(update, context))

    assert db.get_preview(conn, morning_key)["status"] == "pending"
    assert context.bot.edit_message_text.await_count == len(config.admin_user_ids)


def test_on_preview_action_send_now_publishes_immediately(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    context.bot.send_message.reset_mock()

    update = make_update(user_id=111, callback_data=f"prev:{morning_key}:send_now")
    asyncio.run(scheduler.on_preview_action(update, context))

    preview = db.get_preview(conn, morning_key)
    assert preview["status"] == "published"
    assert preview["resolved_by"] == 111
    group_calls = [
        call
        for call in context.bot.send_message.await_args_list
        if call.kwargs.get("chat_id") == config.group_chat_id
    ]
    assert len(group_calls) == 1


def test_on_preview_action_send_fixed_queues_for_configured_time(conn, config, texts, thresholds):
    thresholds = replace(thresholds, autopublish_time=time(23, 59))
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    context.bot.send_message.reset_mock()

    update = make_update(user_id=111, callback_data=f"prev:{morning_key}:send_fixed")
    asyncio.run(scheduler.on_preview_action(update, context))

    preview = db.get_preview(conn, morning_key)
    assert preview["status"] == "queued"
    assert preview["resolved_by"] == 111
    assert preview["scheduled_at"] is not None
    context.bot.send_message.assert_not_called()  # 23:59 ще не настав — не публікує негайно
    edit_calls = context.bot.edit_message_text.await_args_list
    assert any("23:59" in call.kwargs["text"] for call in edit_calls)


def test_on_preview_action_send_fixed_publishes_immediately_if_time_already_passed(
    conn, config, texts, thresholds
):
    thresholds = replace(thresholds, autopublish_time=time(0, 0))
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    context.bot.send_message.reset_mock()

    update = make_update(user_id=111, callback_data=f"prev:{morning_key}:send_fixed")
    asyncio.run(scheduler.on_preview_action(update, context))

    preview = db.get_preview(conn, morning_key)
    assert preview["status"] == "published"


def test_on_preview_action_send_custom_then_text_schedules(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()

    update = make_update(user_id=111, callback_data=f"prev:{morning_key}:send_custom")
    asyncio.run(scheduler.on_preview_action(update, context))
    assert context.user_data[scheduler._SEND_TIME_STEP_KEY] == morning_key
    assert db.get_preview(conn, morning_key)["status"] == "pending"

    tz = ZoneInfo(config.timezone)
    future_label = (datetime.now(tz) + timedelta(minutes=5)).strftime("%H:%M")
    text_update = make_update(user_id=111, text=future_label)
    asyncio.run(scheduler.on_text(text_update, context))

    assert scheduler._SEND_TIME_STEP_KEY not in context.user_data
    preview = db.get_preview(conn, morning_key)
    assert preview["status"] == "queued"
    assert preview["resolved_by"] == 111
    text_update.effective_message.reply_text.assert_awaited_once()


def test_on_preview_action_send_custom_rejects_past_time(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    asyncio.run(
        scheduler.on_preview_action(
            make_update(user_id=111, callback_data=f"prev:{morning_key}:send_custom"), context
        )
    )

    text_update = make_update(user_id=111, text="00:00")
    asyncio.run(scheduler.on_text(text_update, context))

    assert scheduler._SEND_TIME_STEP_KEY in context.user_data  # й досі чекає на новий ввід
    assert db.get_preview(conn, morning_key)["status"] == "pending"
    text_update.effective_message.reply_text.assert_awaited_once_with(
        "Час уже минув. Вкажіть пізніший сьогоднішній час: гг:хх"
    )


def test_on_preview_action_cancel_returns_to_pending(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    send_update = make_update(user_id=111, callback_data=f"prev:{morning_key}:send_now")
    asyncio.run(scheduler.on_preview_action(send_update, context))

    cancel_update = make_update(user_id=222, callback_data=f"prev:{morning_key}:cancel")
    asyncio.run(scheduler.on_preview_action(cancel_update, context))

    preview = db.get_preview(conn, morning_key)
    # send_now уже опублікував (status="published") — "cancel" валиден лише
    # для "queued", тож тут нема ефекту й статус лишається "published".
    assert preview["status"] == "published"


def test_on_preview_action_cancel_after_send_fixed_returns_to_pending(
    conn, config, texts, thresholds
):
    thresholds = replace(thresholds, autopublish_time=time(23, 59))
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    asyncio.run(
        scheduler.on_preview_action(
            make_update(user_id=111, callback_data=f"prev:{morning_key}:send_fixed"), context
        )
    )

    cancel_update = make_update(user_id=222, callback_data=f"prev:{morning_key}:cancel")
    asyncio.run(scheduler.on_preview_action(cancel_update, context))

    preview = db.get_preview(conn, morning_key)
    assert preview["status"] == "pending"
    assert preview["resolved_by"] is None
    assert preview["scheduled_at"] is None


def test_on_preview_action_cancel_then_resend_requeues(conn, config, texts, thresholds):
    thresholds = replace(thresholds, autopublish_time=time(23, 59))
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    asyncio.run(
        scheduler.on_preview_action(
            make_update(user_id=111, callback_data=f"prev:{morning_key}:send_fixed"), context
        )
    )
    asyncio.run(
        scheduler.on_preview_action(
            make_update(user_id=111, callback_data=f"prev:{morning_key}:cancel"), context
        )
    )
    asyncio.run(
        scheduler.on_preview_action(
            make_update(user_id=222, callback_data=f"prev:{morning_key}:send_fixed"), context
        )
    )

    preview = db.get_preview(conn, morning_key)
    assert preview["status"] == "queued"
    assert preview["resolved_by"] == 222


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
    assert context.bot.edit_message_text.await_count == len(config.admin_user_ids)


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
    db.resolve_preview(conn, morning_key, "skipped", 111, "2026-01-01T00:00:00+00:00")

    update = make_update(user_id=222, callback_data=f"prev:{morning_key}:send")
    asyncio.run(scheduler.on_preview_action(update, context))

    update.callback_query.answer.assert_awaited_once_with("Вже оброблено")


def test_on_preview_action_cancel_invalid_when_pending(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()

    update = make_update(user_id=111, callback_data=f"prev:{morning_key}:cancel")
    asyncio.run(scheduler.on_preview_action(update, context))

    update.callback_query.answer.assert_awaited_once_with("Вже оброблено")
    assert db.get_preview(conn, morning_key)["status"] == "pending"


def _force_triggered(conn, morning_key: str) -> None:
    conn.execute("UPDATE preview_state SET triggered = 1 WHERE morning_date = ?", (morning_key,))
    conn.commit()


def test_job_autopublish_noop_when_not_triggered(conn, config, texts, thresholds):
    """Не важка ніч (дефолт без тривог у тестовій БД) і ніхто не натиснув
    'Надіслати' — о 7:30 тиша, це правильний результат, не збій."""
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    context.bot.send_message.reset_mock()

    asyncio.run(scheduler.job_autopublish(context))

    context.bot.send_message.assert_not_called()


_PAST_ISO_UTC = "2020-01-01T00:00:00+00:00"
_FAR_FUTURE_ISO_UTC = "2099-01-01T00:00:00+00:00"


def test_job_publish_queued_publishes_when_scheduled_time_reached(conn, config, texts, thresholds):
    """Головна нова поведінка: адмін обрав час відправки на СПОКІЙНУ ніч
    (алгоритм не тригернув) — коли scheduled_at настав, публікує
    незалежно від triggered. Публікує не job_autopublish (2026-09-20+),
    а окремий поллер job_publish_queued."""
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    db.resolve_preview(conn, morning_key, "queued", 111, _PAST_ISO_UTC, scheduled_at=_PAST_ISO_UTC)
    assert db.get_preview(conn, morning_key)["triggered"] == 0
    context.bot.send_message.reset_mock()

    asyncio.run(scheduler.job_publish_queued(context))

    group_calls = [
        call
        for call in context.bot.send_message.await_args_list
        if call.kwargs.get("chat_id") == config.group_chat_id
    ]
    assert len(group_calls) == 1
    preview = db.get_preview(conn, morning_key)
    assert preview["status"] == "published"
    assert preview["resolved_by"] == 111
    row = conn.execute("SELECT * FROM send_log").fetchone()
    assert row["mode"] == "manual"
    assert row["sent_by"] == 111


def test_job_publish_queued_skips_not_yet_due(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    db.resolve_preview(
        conn, morning_key, "queued", 111, _PAST_ISO_UTC, scheduled_at=_FAR_FUTURE_ISO_UTC
    )
    context.bot.send_message.reset_mock()

    asyncio.run(scheduler.job_publish_queued(context))

    context.bot.send_message.assert_not_called()
    assert db.get_preview(conn, morning_key)["status"] == "queued"


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


def test_publish_retries_then_succeeds_on_transient_network_error(conn, config, texts, thresholds):
    """Регрес: httpx.ReadError (PTB обгортає в NetworkError) на першій
    спробі раніше означало, що ранкове повідомлення взагалі не йшло в
    General до наступного дня (job_autopublish — раз на добу)."""
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    context.bot.send_message = AsyncMock(
        side_effect=[NetworkError("httpx.ReadError: "), MagicMock(message_id=1)]
    )

    with patch("vb_assistant_bot.scheduler.asyncio.sleep", new=AsyncMock()) as sleep_mock:
        asyncio.run(scheduler._publish(context, "текст", "2026-01-01"))

    assert context.bot.send_message.await_count == 2
    sleep_mock.assert_awaited_once()


def test_job_publish_queued_reschedules_when_publish_keeps_failing(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    morning_key = date.today().isoformat()
    db.resolve_preview(conn, morning_key, "queued", 111, _PAST_ISO_UTC, scheduled_at=_PAST_ISO_UTC)

    async def flaky_send(*, chat_id, text, **kwargs):
        if chat_id == config.group_chat_id:
            raise NetworkError("httpx.ReadError: ")
        return MagicMock(message_id=1)

    context.bot.send_message = AsyncMock(side_effect=flaky_send)
    context.job_queue = MagicMock()

    with patch("vb_assistant_bot.scheduler.asyncio.sleep", new=AsyncMock()):
        asyncio.run(scheduler.job_publish_queued(context))

    # Стан лишається "queued" — наступний запуск поллера (реджедул) знову
    # спробує опублікувати те саме, нічого не втрачено.
    assert db.get_preview(conn, morning_key)["status"] == "queued"
    context.job_queue.run_once.assert_called_once_with(scheduler.job_publish_queued, when=300)
    # Адмінів явно попереджено, а не лише мовчки заплановано повтор.
    warn_calls = [
        call
        for call in context.bot.send_message.await_args_list
        if "Не вдалося" in call.kwargs.get("text", "")
    ]
    assert len(warn_calls) == len(config.admin_user_ids)
