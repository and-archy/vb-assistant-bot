import asyncio
from datetime import date

from helpers import make_context, make_update

from vb_assistant_bot import db, input_state, scheduler
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


def test_new_buttons_are_in_keyboard():
    labels = [btn.text for row in menu.MAIN_KEYBOARD.keyboard for btn in row]
    for label in (menu.BTN_STATUS, menu.BTN_SCHEDULED, menu.BTN_HISTORY, menu.BTN_TEXTS):
        assert label in labels
    assert set(labels) == set(menu.BUTTON_LABELS)


def test_on_button_status_replies(conn, config, thresholds):
    update = make_update(user_id=111, text=menu.BTN_STATUS)
    context = make_context(conn, config, thresholds=thresholds)

    asyncio.run(menu.on_button(update, context))

    assert update.effective_message.reply_text.await_args.args[0].startswith("📋 Статус")


def test_on_button_history_and_scheduled_reply(conn, config):
    for label in (menu.BTN_HISTORY, menu.BTN_SCHEDULED):
        update = make_update(user_id=111, text=label)
        asyncio.run(menu.on_button(update, make_context(conn, config)))
        update.effective_message.reply_text.assert_awaited_once()


def test_on_button_texts_shows_sets(conn, config, texts):
    update = make_update(user_id=111, text=menu.BTN_TEXTS)
    asyncio.run(menu.on_button(update, make_context(conn, config, texts=texts)))
    assert "Оберіть набір" in update.effective_message.reply_text.await_args.args[0]


def test_cancel_clears_published_edit_and_texts_input(conn, config):
    for key in (input_state.PUBLISHED_EDIT, input_state.TEXTS_STEP):
        context = make_context(conn, config)
        context.user_data[key] = "x:y"
        update = make_update(user_id=111, text=menu.BTN_CANCEL)

        asyncio.run(menu.on_button(update, context))

        assert key not in context.user_data
        update.effective_message.reply_text.assert_awaited_once_with("Скасовано.")


def test_cancel_with_nothing_pending(conn, config):
    update = make_update(user_id=111, text=menu.BTN_CANCEL)
    asyncio.run(menu.on_button(update, make_context(conn, config)))
    update.effective_message.reply_text.assert_awaited_once_with("Нема чого скасовувати.")


def test_help_mentions_new_features():
    for word in ("Статус", "Заплановані", "Історія", "Тексти", "Видалити з General"):
        assert word in menu.HELP_TEXT
