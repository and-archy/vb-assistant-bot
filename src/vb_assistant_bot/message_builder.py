from vb_assistant_bot.content import Texts, Variant


def build_message(texts: Texts, variant: Variant, *, include_arrangements: bool) -> str:
    """ТЗ п.5: будній день = варіативний блок + домовленості (лише будні,
    п.6) + контакти (п.7). Вихідний день = варіативний блок + контакти."""
    blocks = [variant.text]
    if include_arrangements:
        blocks.append(texts.weekday_arrangements)
    blocks.append(texts.contacts)
    return "\n\n".join(blocks)
