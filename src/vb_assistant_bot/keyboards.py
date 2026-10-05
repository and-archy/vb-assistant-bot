from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def published_keyboard(kind: str, key: str | int) -> InlineKeyboardMarkup:
    """Кнопки під уже опублікованим у General повідомленням (ранковим —
    kind="prev", своїм — kind="custom"): виправити текст або видалити."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ Виправити текст", callback_data=f"pub:{kind}:{key}:edit"),
                InlineKeyboardButton("🗑 Видалити з General", callback_data=f"pub:{kind}:{key}:del"),
            ]
        ]
    )


def delete_confirm_keyboard(kind: str, key: str | int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Так, видалити", callback_data=f"pub:{kind}:{key}:del_yes"),
                InlineKeyboardButton("Ні", callback_data=f"pub:{kind}:{key}:del_no"),
            ]
        ]
    )
