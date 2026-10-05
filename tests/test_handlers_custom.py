import asyncio

from helpers import make_context, make_update

from vb_assistant_bot import db
from vb_assistant_bot.handlers import custom

_FUTURE = "01.01.2099 09:00"
_PAST_ISO_UTC = "2020-01-01T00:00:00+00:00"


def test_start_sets_await_text_step(conn, config):
    update = make_update(user_id=111)
    context = make_context(conn, config)

    asyncio.run(custom.start(update, context))

    assert context.user_data["custom_step"] == "await_text"
    update.effective_message.reply_text.assert_awaited_once()


def test_start_denies_non_admin(conn, config):
    update = make_update(user_id=999)
    context = make_context(conn, config)

    asyncio.run(custom.start(update, context))

    assert "custom_step" not in context.user_data
    update.effective_message.reply_text.assert_awaited_once_with("Немає доступу.")


def test_full_compose_flow_creates_scheduled_message(conn, config):
    context = make_context(conn, config)
    asyncio.run(custom.start(make_update(user_id=111), context))

    text_update = make_update(user_id=111, text="Всім привіт, важлива інформація")
    asyncio.run(custom.on_text(text_update, context))
    assert context.user_data["custom_step"] == "await_time"

    time_update = make_update(user_id=111, text=_FUTURE)
    asyncio.run(custom.on_text(time_update, context))

    assert "custom_step" not in context.user_data
    row = conn.execute("SELECT * FROM custom_messages").fetchone()
    assert row["text"] == "Всім привіт, важлива інформація"
    assert row["status"] == "scheduled"
    assert row["created_by"] == 111
    assert context.bot.send_message.await_count == len(config.admin_user_ids)
    for _, kwargs in context.bot.send_message.await_args_list:
        assert "Заплановане повідомлення" in kwargs["text"]


def test_on_text_ignores_when_no_step_pending(conn, config):
    context = make_context(conn, config)
    update = make_update(user_id=111, text="просто балачка")

    asyncio.run(custom.on_text(update, context))

    update.effective_message.reply_text.assert_not_called()
    context.bot.send_message.assert_not_called()


def test_on_text_ignores_group_chat(conn, config):
    context = make_context(conn, config)
    context.user_data["custom_step"] = "await_text"
    update = make_update(user_id=111, text="в групі", chat_type="group")

    asyncio.run(custom.on_text(update, context))

    assert context.user_data["custom_step"] == "await_text"
    update.effective_message.reply_text.assert_not_called()


def test_invalid_datetime_reprompts(conn, config):
    context = make_context(conn, config)
    asyncio.run(custom.start(make_update(user_id=111), context))
    asyncio.run(custom.on_text(make_update(user_id=111, text="текст"), context))

    bad_update = make_update(user_id=111, text="не дата")
    asyncio.run(custom.on_text(bad_update, context))

    assert context.user_data["custom_step"] == "await_time"
    bad_update.effective_message.reply_text.assert_awaited_once()


def test_past_datetime_rejected(conn, config):
    context = make_context(conn, config)
    asyncio.run(custom.start(make_update(user_id=111), context))
    asyncio.run(custom.on_text(make_update(user_id=111, text="текст"), context))

    past_update = make_update(user_id=111, text="01.01.2020 09:00")
    asyncio.run(custom.on_text(past_update, context))

    assert context.user_data["custom_step"] == "await_time"
    assert conn.execute("SELECT * FROM custom_messages").fetchone() is None


def _create_scheduled(conn, text="текст", scheduled_at=None):
    return db.create_custom_message(
        conn,
        text=text,
        scheduled_at=scheduled_at or "2099-01-01T07:00:00+00:00",
        created_by=111,
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_on_action_cancel(conn, config):
    context = make_context(conn, config)
    custom_id = _create_scheduled(conn)

    update = make_update(user_id=222, callback_data=f"custom:{custom_id}:cancel")
    asyncio.run(custom.on_action(update, context))

    row = db.get_custom_message(conn, custom_id)
    assert row["status"] == "cancelled"
    update.callback_query.answer.assert_awaited_once_with("Скасовано")


def test_on_action_cancel_denies_non_admin(conn, config):
    context = make_context(conn, config)
    custom_id = _create_scheduled(conn)

    update = make_update(user_id=999, callback_data=f"custom:{custom_id}:cancel")
    asyncio.run(custom.on_action(update, context))

    assert db.get_custom_message(conn, custom_id)["status"] == "scheduled"
    update.callback_query.answer.assert_awaited_once_with("Немає доступу", show_alert=True)


def test_on_action_already_resolved_shows_notice(conn, config):
    context = make_context(conn, config)
    custom_id = _create_scheduled(conn)
    db.set_custom_message_status(conn, custom_id, "cancelled", "2026-01-01T00:00:00+00:00")

    update = make_update(user_id=111, callback_data=f"custom:{custom_id}:cancel")
    asyncio.run(custom.on_action(update, context))

    update.callback_query.answer.assert_awaited_once_with("Вже оброблено")


def test_on_action_edit_text_then_on_text_updates(conn, config):
    context = make_context(conn, config)
    custom_id = _create_scheduled(conn, text="старий текст")

    update = make_update(user_id=111, callback_data=f"custom:{custom_id}:edit_text")
    asyncio.run(custom.on_action(update, context))
    assert context.user_data["custom_step"] == "await_edit_text"
    assert context.user_data["custom_edit_id"] == custom_id

    text_update = make_update(user_id=111, text="новий текст")
    asyncio.run(custom.on_text(text_update, context))

    assert db.get_custom_message(conn, custom_id)["text"] == "новий текст"
    assert "custom_step" not in context.user_data


def test_on_action_edit_time_then_on_text_updates(conn, config):
    context = make_context(conn, config)
    custom_id = _create_scheduled(conn, scheduled_at="2099-01-01T00:00:00+00:00")

    update = make_update(user_id=111, callback_data=f"custom:{custom_id}:edit_time")
    asyncio.run(custom.on_action(update, context))
    assert context.user_data["custom_step"] == "await_edit_time"

    time_update = make_update(user_id=111, text="02.01.2099 10:00")
    asyncio.run(custom.on_text(time_update, context))

    row = db.get_custom_message(conn, custom_id)
    assert row["scheduled_at"] != "2099-01-01T00:00:00+00:00"
    assert "custom_step" not in context.user_data


def test_cancel_compose_clears_pending_step(conn, config):
    context = make_context(conn, config)
    context.user_data["custom_step"] = "await_text"
    update = make_update(user_id=111)

    asyncio.run(custom.cancel_compose(update, context))

    assert "custom_step" not in context.user_data
    update.effective_message.reply_text.assert_awaited_once_with("Скасовано.")


def test_cancel_compose_when_nothing_pending(conn, config):
    context = make_context(conn, config)
    update = make_update(user_id=111)

    asyncio.run(custom.cancel_compose(update, context))

    update.effective_message.reply_text.assert_awaited_once_with("Нема чого скасовувати.")


def test_job_dispatch_publishes_due_messages(conn, config):
    context = make_context(conn, config)
    custom_id = _create_scheduled(conn, text="термінове", scheduled_at=_PAST_ISO_UTC)

    asyncio.run(custom.job_dispatch(context))

    row = db.get_custom_message(conn, custom_id)
    assert row["status"] == "sent"
    context.bot.send_message.assert_any_call(chat_id=config.group_chat_id, text="термінове")


def test_job_dispatch_skips_future_messages(conn, config):
    context = make_context(conn, config)
    custom_id = _create_scheduled(conn, scheduled_at="2099-01-01T00:00:00+00:00")

    asyncio.run(custom.job_dispatch(context))

    assert db.get_custom_message(conn, custom_id)["status"] == "scheduled"
    context.bot.send_message.assert_not_called()


def test_view_shows_author_and_who_cancelled(conn, config):
    db.upsert_admin_name(conn, 111, "admin1", "2026-01-01T00:00:00+00:00")
    db.upsert_admin_name(conn, 222, "admin2", "2026-01-01T00:00:00+00:00")
    context = make_context(conn, config)
    custom_id = _create_scheduled(conn)
    db.add_custom_message_preview(conn, custom_id, 111, 10)
    db.add_custom_message_preview(conn, custom_id, 222, 20)

    update = make_update(user_id=222, callback_data=f"custom:{custom_id}:cancel")
    asyncio.run(custom.on_action(update, context))

    assert db.get_custom_message(conn, custom_id)["updated_by"] == 222
    for _, kwargs in context.bot.edit_message_text.await_args_list:
        assert "(admin1, скасував admin2)" in kwargs["text"]


def test_view_shows_who_edited_text(conn, config):
    db.upsert_admin_name(conn, 111, "admin1", "2026-01-01T00:00:00+00:00")
    db.upsert_admin_name(conn, 222, "admin2", "2026-01-01T00:00:00+00:00")
    context = make_context(conn, config)
    custom_id = _create_scheduled(conn)

    asyncio.run(
        custom.on_action(
            make_update(user_id=222, callback_data=f"custom:{custom_id}:edit_text"), context
        )
    )
    asyncio.run(custom.on_text(make_update(user_id=222, text="новий"), context))

    texts_sent = [kwargs["text"] for _, kwargs in context.bot.send_message.await_args_list]
    assert any("(admin1, змінив admin2)" in t for t in texts_sent)


def test_new_message_view_shows_author(conn, config):
    context = make_context(conn, config)
    db.upsert_admin_name(conn, 111, "admin1", "2026-01-01T00:00:00+00:00")
    asyncio.run(custom.start(make_update(user_id=111), context))
    asyncio.run(custom.on_text(make_update(user_id=111, text="текст"), context))
    asyncio.run(custom.on_text(make_update(user_id=111, text=_FUTURE), context))

    for _, kwargs in context.bot.send_message.await_args_list:
        assert "(admin1):" in kwargs["text"]


def test_job_dispatch_failure_notifies_admins_once_then_reports_success(conn, config):
    from telegram.error import NetworkError

    context = make_context(conn, config)
    custom_id = _create_scheduled(conn, text="важливе", scheduled_at=_PAST_ISO_UTC)

    def fail_in_general(chat_id, text, **_):
        if chat_id == config.group_chat_id:
            raise NetworkError("down")

    context.bot.send_message.side_effect = fail_in_general

    asyncio.run(custom.job_dispatch(context))
    asyncio.run(custom.job_dispatch(context))

    admin_texts = [
        kw["text"]
        for _, kw in context.bot.send_message.await_args_list
        if kw["chat_id"] != config.group_chat_id
    ]
    assert len(admin_texts) == len(config.admin_user_ids)
    assert admin_texts[0].startswith("⚠️ Не вдалося опублікувати своє повідомлення")
    assert db.get_custom_message(conn, custom_id)["status"] == "scheduled"

    context.bot.send_message.side_effect = None
    context.bot.send_message.reset_mock()
    asyncio.run(custom.job_dispatch(context))

    row = db.get_custom_message(conn, custom_id)
    assert row["status"] == "sent" and row["publish_failed"] == 0
    admin_texts = [
        kw["text"]
        for _, kw in context.bot.send_message.await_args_list
        if kw.get("chat_id") != config.group_chat_id
    ]
    assert any(t.startswith("✅ Своє повідомлення") for t in admin_texts)
