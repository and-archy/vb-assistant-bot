from unittest.mock import AsyncMock, MagicMock


def make_message(text=None):
    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()
    return message


def make_update(user_id=111, text=None, args=None, callback_data=None):
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id)
    if callback_data is not None:
        message = make_message()
        query = MagicMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()
        query.message = message
        update.callback_query = query
        update.message = None
        update.effective_message = message
    else:
        message = make_message(text=text)
        update.message = message
        update.callback_query = None
        update.effective_message = message
    return update


def make_context(conn, config, texts=None, thresholds=None, args=None):
    context = MagicMock()
    context.bot_data = {"conn": conn, "config": config, "texts": texts, "thresholds": thresholds}
    context.args = args or []
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    context.bot.edit_message_reply_markup = AsyncMock()
    return context
