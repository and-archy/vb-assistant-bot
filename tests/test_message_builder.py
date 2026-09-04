from vb_assistant_bot.message_builder import build_message


def test_weekday_includes_arrangements_and_contacts(texts):
    variant = texts.sets["A"][0]
    message = build_message(texts, variant, include_arrangements=True)
    assert variant.text in message
    assert texts.weekday_arrangements in message
    assert texts.contacts in message
    assert message.index(variant.text) < message.index(texts.weekday_arrangements)
    assert message.index(texts.weekday_arrangements) < message.index(texts.contacts)


def test_weekend_excludes_arrangements(texts):
    variant = texts.sets["V"][0]
    message = build_message(texts, variant, include_arrangements=False)
    assert texts.weekday_arrangements not in message
    assert texts.contacts in message
