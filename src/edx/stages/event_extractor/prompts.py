"""System prompt for the Event Extractor (ТЗ §6)."""

from __future__ import annotations

from edx.config import EventTypesConfig

_STATIC_PREAMBLE: str = """\
Ты — эксперт по разбору сообщений о существенных фактах российских
эмитентов (e-disclosure.ru). Каждое сообщение — это одно событие.
Твоя задача — вернуть строгий JSON по предоставленной схеме.

Правила:
1. Возвращай только то, что прямо подтверждено текстом. Не выдумывай
   даты, имена и числа.
2. event_type — выбери код из списка ниже. Если ни один не подходит,
   используй "other".
3. event_date — фактическая дата события (например, дата сделки или
   решения), формат YYYY-MM-DD. Если дата не указана явно, верни null.
4. publication_date — дата публикации сообщения, YYYY-MM-DD; если в
   тексте не указана, верни null.
5. summary — 1–3 предложения по-русски, не более 600 символов.
   Не пересказывай шапку и реквизиты эмитента.
6. key_params — объект с ключевыми числовыми параметрами (например,
   сумма сделки, размер дивиденда, доля участия). Значения — числа,
   строки или null. Если ключевых параметров нет, верни {}.
7. Возвращай только JSON, без Markdown и пояснительного текста.
"""


def build_system_prompt(event_types_config: EventTypesConfig) -> str:
    """Stable system prompt deterministic from the events catalogue."""
    type_lines: list[str] = []
    for spec in event_types_config.event_types:
        suffix = (
            f" — {spec.description}" if spec.description else ""
        )
        aliases = (
            f" (синонимы: {', '.join(spec.aliases)})" if spec.aliases else ""
        )
        type_lines.append(
            f"- {spec.code}: {spec.display_name}{suffix}{aliases}"
        )
    types_block = "\n".join(type_lines)
    return f"{_STATIC_PREAMBLE.rstrip()}\n\nСправочник типов событий:\n{types_block}\n"
