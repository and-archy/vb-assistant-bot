import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from helpers import make_context, make_update
from telegram.error import NetworkError

from vb_assistant_bot import db, input_state
from vb_assistant_bot.handlers import menu, recurring

_KYIV = ZoneInfo("Europe/Kyiv")
# Середа, 29.10.2026.
_WEDNESDAY_10_05 = datetime(2026, 10, 29, 10, 5, tzinfo=_KYIV)
_LONG_AGO = "2026-01-01T00:00:00+00:00"


def _post(**overrides):
    post = {
        "id": 1,
        "text": "10:00-15:00 «Золоті години»",
        "media_type": "photo",
        "file_id": "photo-id",
        "file_name": None,
        "days": "01234",
        "post_time": "10:00",
        "status": "active",
        "schedule_set_at": _LONG_AGO,
        "last_sent_on": None,
        "last_sent_at": None,
        "publish_failed_on": None,
        "created_by": 111,
        "updated_by": None,
    }
    post.update(overrides)
    return post


def _create(conn, **overrides):
    fields = {
        "text": "10:00-15:00 «Золоті години»",
        "media_type": "photo",
        "file_id": "photo-id",
        "file_name": None,
        "days": "01234",
        "post_time": "10:00",
        "created_by": 111,
        "created_at": _LONG_AGO,
    }
    fields.update(overrides)
    return db.create_recurring_post(conn, **fields)


def _photo_update(caption=None, user_id=111):
    update = make_update(user_id=user_id)
    update.effective_message.photo = [MagicMock(file_id="small"), MagicMock(file_id="big")]
    update.effective_message.document = None
    update.effective_message.caption = caption
    return update


def _context(conn, config, **kwargs):
    context = make_context(conn, config, **kwargs)
    context.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=7))
    context.bot.send_document = AsyncMock(return_value=MagicMock(message_id=8))
    return context


def _freeze(monkeypatch, now_local):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now_local.astimezone(tz) if tz else now_local.replace(tzinfo=None)

    monkeypatch.setattr(recurring, "datetime", _Frozen)


# --- is_due ---------------------------------------------------------------------


def test_is_due_on_matching_weekday_after_time():
    assert recurring.is_due(_post(), _WEDNESDAY_10_05, "workday")


def test_is_due_not_before_time():
    assert not recurring.is_due(_post(), _WEDNESDAY_10_05.replace(hour=9, minute=59), "workday")


def test_is_due_not_after_catch_up_window():
    late = _WEDNESDAY_10_05 + recurring.CATCH_UP
    assert not recurring.is_due(_post(), late, "workday")


def test_is_due_not_twice_a_day():
    assert not recurring.is_due(_post(last_sent_on="2026-10-29"), _WEDNESDAY_10_05, "workday")


def test_is_due_skips_unselected_weekday():
    saturday = datetime(2026, 10, 31, 10, 5, tzinfo=_KYIV)
    assert not recurring.is_due(_post(), saturday, "weekend")


def test_is_due_does_not_catch_up_slot_missed_before_schedule_was_set():
    set_at = (_WEDNESDAY_10_05 - timedelta(minutes=1)).astimezone(UTC).isoformat()
    assert not recurring.is_due(_post(schedule_set_at=set_at), _WEDNESDAY_10_05, "workday")


def test_is_due_skips_paused():
    assert not recurring.is_due(_post(status="paused"), _WEDNESDAY_10_05, "workday")


def test_is_due_workdays_follow_day_type():
    post = _post(days=recurring.WORKDAYS)
    assert recurring.is_due(post, _WEDNESDAY_10_05, "workday")
    assert not recurring.is_due(post, _WEDNESDAY_10_05, "weekend")


# --- next_run / labels -------------------------------------------------------------


def test_next_run_skips_today_after_sending(conn):
    post = _post(last_sent_on="2026-10-29")
    upcoming = recurring.next_run(conn, post, _WEDNESDAY_10_05.replace(hour=9))
    assert upcoming == datetime(2026, 10, 30, 10, 0, tzinfo=_KYIV)


def test_next_run_workdays_respects_manual_holiday(conn):
    db.set_manual_day_type(conn, "2026-10-30", "weekend", 111, _LONG_AGO)
    post = _post(days=recurring.WORKDAYS)
    upcoming = recurring.next_run(conn, post, _WEDNESDAY_10_05)
    # чт 30.10 позначено вихідним, сб/нд — вихідні → пн 02.11.
    assert upcoming == datetime(2026, 11, 2, 10, 0, tzinfo=_KYIV)


def test_days_label():
    assert recurring.days_label("01234") == "пн–пт"
    assert recurring.days_label("0123456") == "щодня"
    assert recurring.days_label("05") == "пн, сб"
    assert recurring.days_label(recurring.WORKDAYS) == "у робочі дні"


# --- створення ------------------------------------------------------------------------


def test_create_flow_photo_then_caption_then_days_then_time(conn, config):
    context = _context(conn, config)
    asyncio.run(recurring.on_action(make_update(user_id=111, callback_data="rec:new"), context))
    assert context.user_data[input_state.RECURRING_STEP] == "await_content"

    asyncio.run(recurring.on_media(_photo_update(), context))
    assert context.user_data[input_state.RECURRING_STEP] == "await_caption"

    asyncio.run(
        recurring.on_text(make_update(user_id=111, text="10:00-15:00 «Золоті години»"), context)
    )
    assert context.user_data[input_state.RECURRING_STEP] == "pick_days"
    assert context.user_data[input_state.RECURRING_DRAFT]["days"] == recurring.WORKDAYS

    asyncio.run(
        recurring.on_action(make_update(user_id=111, callback_data="rec:days:monfri"), context)
    )
    asyncio.run(recurring.on_action(make_update(user_id=111, callback_data="rec:days:ok"), context))
    assert context.user_data[input_state.RECURRING_STEP] == "await_time"

    asyncio.run(recurring.on_text(make_update(user_id=111, text="10:00"), context))

    assert input_state.RECURRING_STEP not in context.user_data
    post = db.recurring_posts(conn)[0]
    assert post["media_type"] == "photo"
    assert post["file_id"] == "big"
    assert post["text"] == "10:00-15:00 «Золоті години»"
    assert post["days"] == "01234"
    assert post["post_time"] == "10:00"
    assert post["status"] == "active"
    shown = [c.kwargs["chat_id"] for c in context.bot.send_message.await_args_list]
    assert set(config.admin_user_ids) <= set(shown)


def test_photo_with_caption_skips_caption_step(conn, config):
    context = _context(conn, config)
    asyncio.run(recurring.on_action(make_update(user_id=111, callback_data="rec:new"), context))
    asyncio.run(recurring.on_media(_photo_update(caption="Підпис"), context))
    assert context.user_data[input_state.RECURRING_STEP] == "pick_days"
    assert context.user_data[input_state.RECURRING_DRAFT]["text"] == "Підпис"


def test_caption_over_telegram_limit_rejected(conn, config):
    context = _context(conn, config)
    asyncio.run(recurring.on_action(make_update(user_id=111, callback_data="rec:new"), context))
    update = _photo_update(caption="я" * 1025)
    asyncio.run(recurring.on_media(update, context))
    assert context.user_data[input_state.RECURRING_STEP] == "await_content"
    assert "задовгий" in update.effective_message.reply_text.await_args.args[0]


def test_toggling_days_and_empty_selection_blocks_next(conn, config):
    context = _context(conn, config)
    context.user_data[input_state.RECURRING_STEP] = "pick_days"
    context.user_data[input_state.RECURRING_DRAFT] = {"days": "0"}
    asyncio.run(recurring.on_action(make_update(user_id=111, callback_data="rec:days:0"), context))
    assert context.user_data[input_state.RECURRING_DRAFT]["days"] == ""

    update = make_update(user_id=111, callback_data="rec:days:ok")
    asyncio.run(recurring.on_action(update, context))
    assert context.user_data[input_state.RECURRING_STEP] == "pick_days"
    update.callback_query.answer.assert_awaited_once_with(
        "Оберіть хоча б один день", show_alert=True
    )


def test_media_ignored_without_active_step(conn, config):
    context = _context(conn, config)
    update = _photo_update()
    asyncio.run(recurring.on_media(update, context))
    update.effective_message.reply_text.assert_not_called()


def test_non_admin_cannot_press_buttons(conn, config):
    context = _context(conn, config)
    update = make_update(user_id=999, callback_data="rec:new")
    asyncio.run(recurring.on_action(update, context))
    update.callback_query.answer.assert_awaited_once_with("Немає доступу", show_alert=True)
    assert input_state.RECURRING_STEP not in context.user_data


# --- зміни ------------------------------------------------------------------------------


def test_edit_days_updates_post_and_resets_schedule_anchor(conn, config):
    pid = _create(conn)
    context = _context(conn, config)
    asyncio.run(
        recurring.on_action(make_update(user_id=222, callback_data=f"rec:{pid}:edit_days"), context)
    )
    asyncio.run(
        recurring.on_action(make_update(user_id=222, callback_data="rec:days:all"), context)
    )
    asyncio.run(recurring.on_action(make_update(user_id=222, callback_data="rec:days:ok"), context))

    post = db.get_recurring_post(conn, pid)
    assert post["days"] == "0123456"
    assert post["updated_by"] == 222
    assert post["schedule_set_at"] > _LONG_AGO
    assert input_state.RECURRING_STEP not in context.user_data


def test_edit_media_with_caption_replaces_text_too(conn, config):
    pid = _create(conn)
    context = _context(conn, config)
    asyncio.run(
        recurring.on_action(
            make_update(user_id=111, callback_data=f"rec:{pid}:edit_media"), context
        )
    )
    asyncio.run(recurring.on_media(_photo_update(caption="Новий підпис"), context))

    post = db.get_recurring_post(conn, pid)
    assert post["file_id"] == "big"
    assert post["text"] == "Новий підпис"


def test_remove_media_requires_text(conn, config):
    pid = _create(conn, text="")
    context = _context(conn, config)
    asyncio.run(
        recurring.on_action(
            make_update(user_id=111, callback_data=f"rec:{pid}:edit_media"), context
        )
    )
    asyncio.run(recurring.on_text(make_update(user_id=111, text="-"), context))
    assert db.get_recurring_post(conn, pid)["media_type"] == "photo"


def test_edit_time(conn, config):
    pid = _create(conn)
    context = _context(conn, config)
    asyncio.run(
        recurring.on_action(make_update(user_id=111, callback_data=f"rec:{pid}:edit_time"), context)
    )
    asyncio.run(recurring.on_text(make_update(user_id=111, text="9:30"), context))
    assert db.get_recurring_post(conn, pid)["post_time"] == "09:30"


def test_pause_and_delete(conn, config):
    pid = _create(conn)
    context = _context(conn, config)
    asyncio.run(
        recurring.on_action(make_update(user_id=111, callback_data=f"rec:{pid}:pause"), context)
    )
    assert db.get_recurring_post(conn, pid)["status"] == "paused"

    asyncio.run(
        recurring.on_action(make_update(user_id=111, callback_data=f"rec:{pid}:del_yes"), context)
    )
    assert db.get_recurring_post(conn, pid)["status"] == "deleted"
    assert db.recurring_posts(conn) == []


def test_show_sends_real_post_only_to_presser(conn, config):
    pid = _create(conn)
    context = _context(conn, config)
    asyncio.run(
        recurring.on_action(make_update(user_id=222, callback_data=f"rec:{pid}:show"), context)
    )
    context.bot.send_photo.assert_awaited_once_with(
        chat_id=222, photo="photo-id", caption="10:00-15:00 «Золоті години»"
    )


def test_update_rejects_unknown_columns(conn):
    pid = _create(conn)
    try:
        db.update_recurring_post(conn, pid, updated_by=1, updated_at=_LONG_AGO, created_by=5)
    except ValueError:
        return
    raise AssertionError("очікувався ValueError")


# --- публікація -------------------------------------------------------------------------


def test_dispatch_publishes_photo_with_caption_once(conn, config, monkeypatch):
    pid = _create(conn)
    _freeze(monkeypatch, _WEDNESDAY_10_05)
    context = _context(conn, config)

    asyncio.run(recurring.job_dispatch(context))
    asyncio.run(recurring.job_dispatch(context))

    context.bot.send_photo.assert_awaited_once_with(
        chat_id=config.group_chat_id, photo="photo-id", caption="10:00-15:00 «Золоті години»"
    )
    assert db.get_recurring_post(conn, pid)["last_sent_on"] == "2026-10-29"


def test_dispatch_text_only_post(conn, config, monkeypatch):
    _create(conn, media_type=None, file_id=None, text="Просто текст")
    _freeze(monkeypatch, _WEDNESDAY_10_05)
    context = _context(conn, config)
    asyncio.run(recurring.job_dispatch(context))
    assert any(
        c.kwargs == {"chat_id": config.group_chat_id, "text": "Просто текст"}
        for c in context.bot.send_message.await_args_list
    )


def test_dispatch_skips_manual_holiday_for_workdays(conn, config, monkeypatch):
    _create(conn, days=recurring.WORKDAYS)
    db.set_manual_day_type(conn, "2026-10-29", "weekend", 111, _LONG_AGO)
    _freeze(monkeypatch, _WEDNESDAY_10_05)
    context = _context(conn, config)
    asyncio.run(recurring.job_dispatch(context))
    context.bot.send_photo.assert_not_called()


def test_dispatch_failure_notifies_once_then_success_notifies(conn, config, monkeypatch):
    pid = _create(conn)
    _freeze(monkeypatch, _WEDNESDAY_10_05)
    context = _context(conn, config)
    context.bot.send_photo = AsyncMock(side_effect=NetworkError("timeout"))

    asyncio.run(recurring.job_dispatch(context))
    asyncio.run(recurring.job_dispatch(context))
    warnings = [c for c in context.bot.send_message.await_args_list if "⚠️" in c.kwargs["text"]]
    assert len(warnings) == len(config.admin_user_ids)
    assert "до 11:00" in warnings[0].kwargs["text"]

    context.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=7))
    asyncio.run(recurring.job_dispatch(context))
    successes = [c for c in context.bot.send_message.await_args_list if "✅" in c.kwargs["text"]]
    assert len(successes) == len(config.admin_user_ids)
    post = db.get_recurring_post(conn, pid)
    assert post["last_sent_on"] == "2026-10-29"
    assert post["publish_failed_on"] is None


# --- меню й скасування -----------------------------------------------------------------


def test_menu_button_shows_list(conn, config):
    _create(conn)
    context = _context(conn, config)
    update = make_update(user_id=111, text=menu.BTN_RECURRING)
    asyncio.run(menu.on_button(update, context))
    assert "Регулярні повідомлення: 1" in update.effective_message.reply_text.await_args.args[0]
    assert context.bot.send_message.await_count == 1


def test_cancel_clears_recurring_input(conn, config):
    context = _context(conn, config)
    context.user_data[input_state.RECURRING_STEP] = "await_content"
    context.user_data[input_state.RECURRING_DRAFT] = {}
    update = make_update(user_id=111)
    asyncio.run(menu.cancel(update, context))
    update.effective_message.reply_text.assert_awaited_once_with("Скасовано.")
    assert input_state.RECURRING_DRAFT not in context.user_data
