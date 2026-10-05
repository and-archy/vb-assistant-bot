"""Тексти ранкових повідомлень = config/texts.json + зміни, зроблені
адмінами через бота (кнопка «✏️ Тексти», таблиця text_variants).
Файл лишається базою: варіант із файлу можна вимкнути або змінити його
текст (і повернути текст із файлу), варіант, доданий через бота, — ще й
видалити. Блоки «домовленості» й «контакти» через бота не редагуються."""

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from vb_assistant_bot import db
from vb_assistant_bot.content import Texts, Variant

SET_LABELS = {"A": "будні дні", "V": "вихідні дні", "B": "стриманий тон"}


@dataclass(frozen=True)
class VariantInfo:
    id: str
    set_name: str
    text: str
    enabled: bool
    from_bot: bool
    edited: bool  # текст варіанта з файлу змінено через бота


def set_label(set_name: str) -> str:
    return SET_LABELS.get(set_name, set_name)


def variants_by_set(file_texts: Texts, conn) -> dict[str, list[VariantInfo]]:
    overrides = {row["variant_id"]: row for row in db.text_variant_overrides(conn)}
    result: dict[str, list[VariantInfo]] = {}
    for set_name, variants in file_texts.sets.items():
        items = []
        for variant in variants:
            row = overrides.pop(variant.id, None)
            if row is None:
                items.append(VariantInfo(variant.id, set_name, variant.text, True, False, False))
                continue
            text = row["text"] if row["text"] is not None else variant.text
            items.append(
                VariantInfo(
                    variant.id,
                    set_name,
                    text,
                    bool(row["enabled"]),
                    False,
                    row["text"] is not None and row["text"] != variant.text,
                )
            )
        result[set_name] = items
    for row in overrides.values():
        if not row["from_bot"] or row["set_name"] not in result or row["text"] is None:
            continue  # залишок від варіанта, якого вже немає у файлі
        result[row["set_name"]].append(
            VariantInfo(
                row["variant_id"], row["set_name"], row["text"], bool(row["enabled"]), True, False
            )
        )
    return result


def find(file_texts: Texts, conn, variant_id: str) -> VariantInfo | None:
    for items in variants_by_set(file_texts, conn).values():
        for item in items:
            if item.id == variant_id:
                return item
    return None


def effective_texts(file_texts: Texts, conn) -> Texts:
    """Те, що реально використовує колода: лише увімкнені варіанти. Набір
    ніколи не буває порожнім — хендлер не дає вимкнути/видалити останній."""
    sets = {
        set_name: tuple(Variant(id=i.id, text=i.text) for i in items if i.enabled)
        for set_name, items in variants_by_set(file_texts, conn).items()
    }
    return replace(file_texts, sets=sets)


def next_variant_id(file_texts: Texts, conn, set_name: str) -> str:
    numbers = [
        int(match.group(1))
        for item in variants_by_set(file_texts, conn)[set_name]
        if (match := re.fullmatch(rf"{re.escape(set_name)}(\d+)", item.id))
    ]
    return f"{set_name}{max(numbers, default=0) + 1}"


def save(conn, info: VariantInfo, *, file_text: str | None, user_id: int) -> None:
    """file_text — текст цього варіанта у файлі (None для доданих через
    бота); якщо текст збігається з файлом, у БД зберігається NULL."""
    text = None if file_text is not None and info.text == file_text else info.text
    if file_text is not None and text is None and info.enabled:
        db.delete_text_variant(conn, info.id)  # повністю як у файлі
        return
    db.upsert_text_variant(
        conn,
        variant_id=info.id,
        set_name=info.set_name,
        text=text,
        enabled=info.enabled,
        from_bot=info.from_bot,
        updated_by=user_id,
        updated_at=datetime.now(UTC).isoformat(),
    )


def file_text(file_texts: Texts, variant_id: str) -> str | None:
    for variants in file_texts.sets.values():
        for variant in variants:
            if variant.id == variant_id:
                return variant.text
    return None
