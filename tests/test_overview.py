import asyncio
from datetime import date

from helpers import make_context, make_update

from vb_assistant_bot import audit, db, health, scheduler
from vb_assistant_bot.handlers import overview


def test_status_contains_all_sections(conn, config, texts, thresholds):
    db.upsert_admin_name(conn, 222, "admin2", "2026-01-01T00:00:00+00:00")
    db.set_manual_day_type(conn, date.today().isoformat(), "weekend", 222, "2026-01-01T00:00:00")
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    db.resolve_preview(conn, date.today().isoformat(), "skipped", 222, "2026-01-01T00:00:00")

    text = overview.build_status(context)

    assert "вихідний день (позначено вручну (admin2))" in text
    assert "Ранкове прев'ю: пропущено (admin2)" in text
    assert "Автопублікація: вимкнена" in text
    assert "Дані про тривоги" in text
    assert "Заплановані свої повідомлення: немає" in text
    assert "Помилки в боті: немає" in text


def test_status_shows_error_series(conn, config, thresholds):
    context = make_context(conn, config, thresholds=thresholds)
    health.record_error(context.bot_data)

    assert "⚠️ 1 з" in overview.build_status(context)


def test_history_shows_actions_with_names(conn, config):
    db.upsert_admin_name(conn, 111, "admin1", "2026-01-01T00:00:00+00:00")
    audit.log(conn, 111, "пропустив ранкове прев'ю")
    audit.log(conn, None, "автопублікація ранкового повідомлення в General")
    context = make_context(conn, config)
    update = make_update(user_id=111)

    asyncio.run(overview.history(update, context))

    text = update.effective_message.reply_text.await_args.args[0]
    lines = text.splitlines()
    assert "🤖 бот — автопублікація" in lines[2]  # нові зверху
    assert "admin1 — пропустив ранкове прев'ю" in lines[3]


def test_history_empty(conn, config):
    update = make_update(user_id=111)
    asyncio.run(overview.history(update, make_context(conn, config)))
    assert "дій не було" in update.effective_message.reply_text.await_args.args[0]


def test_scheduled_empty(conn, config):
    update = make_update(user_id=111)
    asyncio.run(overview.scheduled(update, make_context(conn, config)))
    update.effective_message.reply_text.assert_awaited_once_with("🗓 Запланованих публікацій немає.")


def test_scheduled_resends_views_and_takes_over_updates(conn, config, texts, thresholds):
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    custom_id = db.create_custom_message(
        conn,
        text="текст",
        scheduled_at="2099-01-01T07:00:00+00:00",
        created_by=111,
        created_at="2026-01-01T00:00:00+00:00",
    )
    asyncio.run(scheduler.generate_and_send_preview(context, force=True))
    key = date.today().isoformat()
    db.resolve_preview(conn, key, "queued", 111, "x", scheduled_at="2099-01-01T05:00:00+00:00")
    context.bot.send_message.reset_mock()
    context.bot.send_message.return_value.message_id = 4242

    asyncio.run(overview.scheduled(make_update(user_id=222), context))

    assert context.bot.send_message.await_count == 2
    previews = {r["chat_id"]: r["message_id"] for r in db.preview_messages(conn, key)}
    assert previews[222] == 4242
    customs = {r["chat_id"]: r["message_id"] for r in db.custom_message_previews(conn, custom_id)}
    assert customs[222] == 4242
