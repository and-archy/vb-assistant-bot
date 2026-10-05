import asyncio

from helpers import make_context, make_update

from vb_assistant_bot import db, input_state, text_store
from vb_assistant_bot.handlers import texts as texts_handler


def _ctx(conn, config, texts):
    context = make_context(conn, config, texts=texts)
    context.bot_data["file_texts"] = texts
    return context


def _click(context, data, user_id=111):
    update = make_update(user_id=user_id, callback_data=data)
    asyncio.run(texts_handler.on_action(update, context))
    return update


def test_effective_texts_equal_file_without_changes(conn, texts):
    assert text_store.effective_texts(texts, conn).sets == texts.sets


def test_disable_variant_removes_it_from_effective_texts(conn, config, texts):
    context = _ctx(conn, config, texts)
    first = texts.sets["A"][0].id

    _click(context, f"txt:toggle:{first}")

    assert first not in [v.id for v in context.bot_data["texts"].sets["A"]]
    assert text_store.find(texts, conn, first).enabled is False

    _click(context, f"txt:toggle:{first}")
    assert first in [v.id for v in context.bot_data["texts"].sets["A"]]
    assert db.text_variant_overrides(conn) == []  # знову як у файлі


def test_cannot_disable_last_enabled_variant(conn, config, texts):
    context = _ctx(conn, config, texts)
    ids = [v.id for v in texts.sets["B"]]
    for variant_id in ids[:-1]:
        _click(context, f"txt:toggle:{variant_id}")

    update = _click(context, f"txt:toggle:{ids[-1]}")

    assert text_store.find(texts, conn, ids[-1]).enabled is True
    assert update.callback_query.answer.await_args.kwargs["show_alert"] is True


def test_add_variant_via_text(conn, config, texts):
    context = _ctx(conn, config, texts)
    _click(context, "txt:add:A")
    assert context.user_data[input_state.TEXTS_STEP] == "add:A"

    asyncio.run(texts_handler.on_text(make_update(user_id=111, text="Новий текст"), context))

    new_id = f"A{len(texts.sets['A']) + 1}"
    info = text_store.find(texts, conn, new_id)
    assert info is not None and info.from_bot and info.text == "Новий текст"
    assert new_id in [v.id for v in context.bot_data["texts"].sets["A"]]
    assert input_state.TEXTS_STEP not in context.user_data


def test_edit_and_reset_file_variant(conn, config, texts):
    context = _ctx(conn, config, texts)
    variant = texts.sets["V"][0]

    _click(context, f"txt:edit:{variant.id}")
    asyncio.run(texts_handler.on_text(make_update(user_id=111, text="Змінено"), context))
    info = text_store.find(texts, conn, variant.id)
    assert info.text == "Змінено" and info.edited

    _click(context, f"txt:reset:{variant.id}")
    assert text_store.find(texts, conn, variant.id).text == variant.text
    assert db.text_variant_overrides(conn) == []


def test_delete_bot_added_variant(conn, config, texts):
    context = _ctx(conn, config, texts)
    _click(context, "txt:add:B")
    asyncio.run(texts_handler.on_text(make_update(user_id=111, text="тимчасовий"), context))
    new_id = f"B{len(texts.sets['B']) + 1}"

    _click(context, f"txt:del:{new_id}")

    assert text_store.find(texts, conn, new_id) is None


def test_changes_are_logged(conn, config, texts):
    context = _ctx(conn, config, texts)
    _click(context, f"txt:toggle:{texts.sets['A'][0].id}")
    actions = [r["action"] for r in db.recent_actions(conn, "2000-01-01", 10)]
    assert actions == [f"вимкнув варіант {texts.sets['A'][0].id}"]


def test_show_lists_sets(conn, config, texts):
    context = _ctx(conn, config, texts)
    update = make_update(user_id=111)

    asyncio.run(texts_handler.show(update, context))

    body = update.effective_message.reply_text.await_args.args[0]
    assert "A — будні дні" in body and "B — стриманий тон" in body


def test_non_admin_denied(conn, config, texts):
    context = _ctx(conn, config, texts)
    update = _click(context, f"txt:toggle:{texts.sets['A'][0].id}", user_id=999)
    update.callback_query.answer.assert_awaited_once_with("Немає доступу", show_alert=True)
    assert db.text_variant_overrides(conn) == []
