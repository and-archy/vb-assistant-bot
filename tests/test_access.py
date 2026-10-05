import asyncio

from helpers import make_context, make_update

from vb_assistant_bot import db
from vb_assistant_bot.access import admin_label, display_name, ensure_admin, remember_admin


def test_ensure_admin_allows_admin(config):
    update = make_update(user_id=111)
    assert asyncio.run(ensure_admin(update, config)) is True
    update.effective_message.reply_text.assert_not_called()


def test_ensure_admin_denies_non_admin(config):
    update = make_update(user_id=999)
    assert asyncio.run(ensure_admin(update, config)) is False
    update.effective_message.reply_text.assert_awaited_once_with("Немає доступу.")


def test_remember_admin_stores_username(conn, config):
    context = make_context(conn, config)

    asyncio.run(remember_admin(make_update(user_id=111), context))

    assert db.get_admin_name(conn, 111) == "admin111"


def test_remember_admin_ignores_non_admin(conn, config):
    context = make_context(conn, config)

    asyncio.run(remember_admin(make_update(user_id=999), context))

    assert db.get_admin_name(conn, 999) is None


def test_display_name_falls_back_to_full_name():
    user = make_update(user_id=111).effective_user
    user.username = None
    assert display_name(user) == "Admin 111"


def test_admin_label_unknown_admin_shows_id(conn):
    assert admin_label(conn, 555) == "id 555"
    assert admin_label(conn, None) is None
