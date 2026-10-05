import asyncio
from datetime import date

from helpers import make_context, make_update
from telegram.error import BadRequest

from vb_assistant_bot import db, input_state, scheduler
from vb_assistant_bot.handlers import custom, published


def _published_preview(conn, config, texts, thresholds, group_message_id=555):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    key = date.today().isoformat()
    db.resolve_preview(conn, key, "published", 111, "2026-10-05T04:00:00+00:00")
    db.set_preview_group_message(conn, key, group_message_id)
    context.bot.send_message.reset_mock()
    context.bot.edit_message_text.reset_mock()
    return context, key


def _buttons(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_published_preview_view_has_edit_and_delete_buttons(conn, config, texts, thresholds):
    _, key = _published_preview(conn, config, texts, thresholds)

    _, keyboard = scheduler.preview_view(conn, key, config.timezone)

    assert _buttons(keyboard) == [f"pub:prev:{key}:edit", f"pub:prev:{key}:del"]


def test_publish_stores_group_message_id(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    key = date.today().isoformat()
    context.bot.send_message.return_value.message_id = 777

    update = make_update(user_id=111, callback_data=f"prev:{key}:send_now")
    asyncio.run(scheduler.on_preview_action(update, context))

    assert db.get_preview(conn, key)["group_message_id"] == 777
    log = conn.execute("SELECT group_message_id FROM send_log").fetchone()
    assert log["group_message_id"] == 777


def test_delete_asks_confirmation_first(conn, config, texts, thresholds):
    context, key = _published_preview(conn, config, texts, thresholds)

    update = make_update(user_id=222, callback_data=f"pub:prev:{key}:del")
    asyncio.run(published.on_action(update, context))

    context.bot.delete_message.assert_not_called()
    markup = update.callback_query.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    assert f"pub:prev:{key}:del_yes" in _buttons(markup)


def test_delete_confirmed_removes_from_general(conn, config, texts, thresholds):
    db.upsert_admin_name(conn, 222, "admin2", "2026-01-01T00:00:00+00:00")
    context, key = _published_preview(conn, config, texts, thresholds)

    update = make_update(user_id=222, callback_data=f"pub:prev:{key}:del_yes")
    asyncio.run(published.on_action(update, context))

    context.bot.delete_message.assert_awaited_once_with(
        chat_id=config.group_chat_id, message_id=555
    )
    preview = db.get_preview(conn, key)
    assert preview["status"] == "deleted"
    assert preview["resolved_by"] == 111
    texts_sent = [kw["text"] for _, kw in context.bot.edit_message_text.await_args_list]
    assert texts_sent and all("🗑 Видалено з General (admin2)" in t for t in texts_sent)


def test_delete_failure_shows_alert_and_keeps_status(conn, config, texts, thresholds):
    context, key = _published_preview(conn, config, texts, thresholds)
    context.bot.delete_message.side_effect = BadRequest("Message can't be deleted")

    update = make_update(user_id=222, callback_data=f"pub:prev:{key}:del_yes")
    asyncio.run(published.on_action(update, context))

    assert db.get_preview(conn, key)["status"] == "published"
    _, kwargs = update.callback_query.answer.await_args
    assert kwargs["show_alert"] is True


def test_edit_published_preview_text(conn, config, texts, thresholds):
    db.upsert_admin_name(conn, 222, "admin2", "2026-01-01T00:00:00+00:00")
    context, key = _published_preview(conn, config, texts, thresholds)

    asyncio.run(
        published.on_action(make_update(user_id=222, callback_data=f"pub:prev:{key}:edit"), context)
    )
    assert context.user_data[input_state.PUBLISHED_EDIT] == f"prev:{key}"

    reply = make_update(user_id=222, text="Виправлений текст")
    asyncio.run(published.on_text(reply, context))

    context.bot.edit_message_text.assert_any_await(
        chat_id=config.group_chat_id, message_id=555, text="Виправлений текст"
    )
    preview = db.get_preview(conn, key)
    assert preview["message_text"] == "Виправлений текст"
    assert preview["actor_action"] == "edited"
    assert input_state.PUBLISHED_EDIT not in context.user_data
    views = [kw.get("text") for _, kw in context.bot.edit_message_text.await_args_list]
    assert any("✏️ Текст у General виправив: admin2" in (t or "") for t in views)


def test_edit_and_delete_published_custom_message(conn, config):
    context = make_context(conn, config)
    custom_id = db.create_custom_message(
        conn,
        text="старе",
        scheduled_at="2020-01-01T00:00:00+00:00",
        created_by=111,
        created_at="2020-01-01T00:00:00+00:00",
    )
    context.bot.send_message.return_value.message_id = 900
    asyncio.run(custom.job_dispatch(context))
    assert db.get_custom_message(conn, custom_id)["group_message_id"] == 900

    asyncio.run(
        published.on_action(
            make_update(user_id=222, callback_data=f"pub:custom:{custom_id}:edit"), context
        )
    )
    asyncio.run(published.on_text(make_update(user_id=222, text="нове"), context))
    row = db.get_custom_message(conn, custom_id)
    assert row["text"] == "нове"
    assert row["updated_by"] == 222

    asyncio.run(
        published.on_action(
            make_update(user_id=222, callback_data=f"pub:custom:{custom_id}:del_yes"), context
        )
    )
    assert db.get_custom_message(conn, custom_id)["status"] == "deleted"
    context.bot.delete_message.assert_awaited_once_with(
        chat_id=config.group_chat_id, message_id=900
    )


def test_action_on_not_published_is_rejected(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    key = date.today().isoformat()

    update = make_update(user_id=111, callback_data=f"pub:prev:{key}:del_yes")
    asyncio.run(published.on_action(update, context))

    update.callback_query.answer.assert_awaited_once_with("Вже неактуально")
    context.bot.delete_message.assert_not_called()


def test_non_admin_denied(conn, config, texts, thresholds):
    context, key = _published_preview(conn, config, texts, thresholds)

    update = make_update(user_id=999, callback_data=f"pub:prev:{key}:del_yes")
    asyncio.run(published.on_action(update, context))

    context.bot.delete_message.assert_not_called()
