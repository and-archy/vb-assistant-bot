import asyncio
from datetime import date

from helpers import make_context, make_update

from vb_assistant_bot import db, scheduler
from vb_assistant_bot.handlers import menu


def test_start_sends_help_with_keyboard(conn, config):
    update = make_update(user_id=111)
    context = make_context(conn, config)

    asyncio.run(menu.start(update, context))

    update.effective_message.reply_text.assert_awaited_once()
    _, kwargs = update.effective_message.reply_text.await_args
    assert kwargs["reply_markup"] is menu.MAIN_KEYBOARD


def test_start_denies_non_admin(conn, config):
    update = make_update(user_id=999)
    context = make_context(conn, config)

    asyncio.run(menu.start(update, context))

    update.effective_message.reply_text.assert_awaited_once_with("Немає доступу.")


def test_on_button_ignores_non_private_chat(conn, config):
    update = make_update(user_id=111, text=menu.BTN_HELP, chat_type="group")
    context = make_context(conn, config)

    asyncio.run(menu.on_button(update, context))

    update.effective_message.reply_text.assert_not_called()


def test_on_button_support_triggers_preview(conn, config, texts, thresholds):
    update = make_update(user_id=111, text=menu.BTN_SUPPORT)
    context = make_context(conn, config, texts=texts, thresholds=thresholds)

    asyncio.run(menu.on_button(update, context))

    assert db.get_preview(conn, date.today().isoformat()) is not None


def test_on_button_custom_starts_compose(conn, config):
    update = make_update(user_id=111, text=menu.BTN_CUSTOM)
    context = make_context(conn, config)

    asyncio.run(menu.on_button(update, context))

    assert context.user_data["custom_step"] == "await_text"


def test_on_button_weekend_marks_today(conn, config):
    update = make_update(user_id=111, text=menu.BTN_WEEKEND)
    context = make_context(conn, config)

    asyncio.run(menu.on_button(update, context))

    assert db.get_manual_day_type(conn, date.today().isoformat()) == "weekend"


def test_on_button_workday_marks_today(conn, config):
    update = make_update(user_id=111, text=menu.BTN_WORKDAY)
    context = make_context(conn, config)

    asyncio.run(menu.on_button(update, context))

    assert db.get_manual_day_type(conn, date.today().isoformat()) == "workday"


def test_on_button_cancel_clears_pending_compose(conn, config):
    update = make_update(user_id=111, text=menu.BTN_CANCEL)
    context = make_context(conn, config)
    context.user_data["custom_step"] = "await_text"

    asyncio.run(menu.on_button(update, context))

    assert "custom_step" not in context.user_data
    update.effective_message.reply_text.assert_awaited_once_with("Скасовано.")


def test_on_button_help_resends_help(conn, config):
    update = make_update(user_id=111, text=menu.BTN_HELP)
    context = make_context(conn, config)

    asyncio.run(menu.on_button(update, context))

    update.effective_message.reply_text.assert_awaited_once()


def test_on_button_cancel_clears_pending_send_time_prompt(conn, config):
    update = make_update(user_id=111, text=menu.BTN_CANCEL)
    context = make_context(conn, config)
    context.user_data[scheduler._SEND_TIME_STEP_KEY] = "2026-09-20"

    asyncio.run(menu.on_button(update, context))

    assert scheduler._SEND_TIME_STEP_KEY not in context.user_data
    update.effective_message.reply_text.assert_awaited_once_with("Скасовано.")


def test_on_button_switching_away_clears_stale_send_time_prompt(conn, config):
    update = make_update(user_id=111, text=menu.BTN_HELP)
    context = make_context(conn, config)
    context.user_data[scheduler._SEND_TIME_STEP_KEY] = "2026-09-20"

    asyncio.run(menu.on_button(update, context))

    assert scheduler._SEND_TIME_STEP_KEY not in context.user_data


def test_on_button_switching_away_from_custom_clears_stale_state(conn, config, texts, thresholds):
    """Регрес: перемикання на іншу кнопку під час незавершеного /custom
    не повинно лишати "привида" custom_step — інакше наступне звичайне
    повідомлення адміна хибно зчиталось би як текст/час свого
    повідомлення."""
    update = make_update(user_id=111, text=menu.BTN_SUPPORT)
    context = make_context(conn, config, texts=texts, thresholds=thresholds)
    context.user_data["custom_step"] = "await_text"
    context.user_data["custom_text"] = "недописане"

    asyncio.run(menu.on_button(update, context))

    assert "custom_step" not in context.user_data
    assert "custom_text" not in context.user_data
