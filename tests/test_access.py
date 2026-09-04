import asyncio

from helpers import make_update

from vb_assistant_bot.access import ensure_admin


def test_ensure_admin_allows_admin(config):
    update = make_update(user_id=111)
    assert asyncio.run(ensure_admin(update, config)) is True
    update.effective_message.reply_text.assert_not_called()


def test_ensure_admin_denies_non_admin(config):
    update = make_update(user_id=999)
    assert asyncio.run(ensure_admin(update, config)) is False
    update.effective_message.reply_text.assert_awaited_once_with("Немає доступу.")
