"""Ключі user_data, якими хендлери позначають «чекаю від адміна текст».
Одночасно активним має бути лише один такий стан — інакше одне
повідомлення адміна зчиталось би кількома хендлерами (кожен у своїй
групі PTB). Тому кожен потік вводу перед стартом викликає `clear_all`."""

CUSTOM_STEP = "custom_step"
CUSTOM_TEXT = "custom_text"
CUSTOM_EDIT_ID = "custom_edit_id"
PREVIEW_SEND_TIME = "prev_send_time_for"
PUBLISHED_EDIT = "pub_edit"
TEXTS_STEP = "texts_step"

_ALL = (CUSTOM_STEP, CUSTOM_TEXT, CUSTOM_EDIT_ID, PREVIEW_SEND_TIME, PUBLISHED_EDIT, TEXTS_STEP)
# Ключі, що самі по собі означають «очікується ввід» (CUSTOM_TEXT/EDIT_ID
# — лише супутні дані до CUSTOM_STEP).
_WAITING = (CUSTOM_STEP, PREVIEW_SEND_TIME, PUBLISHED_EDIT, TEXTS_STEP)


def any_active(user_data: dict) -> bool:
    return any(user_data.get(key) for key in _WAITING)


def clear_all(user_data: dict) -> None:
    for key in _ALL:
        user_data.pop(key, None)
