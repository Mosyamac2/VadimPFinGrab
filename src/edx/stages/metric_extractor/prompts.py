"""Build the LLM system prompt from :class:`MetricsConfig`.

The static preamble is at the top so prompt-caching wins are maximised across
calls. The dynamic section enumerates the metric catalogue from
``metrics.yaml`` — operators add new metrics there without touching code.
"""

from __future__ import annotations

from edx.config import MetricsConfig

_STATIC_PREAMBLE: str = """\
Ты — эксперт по извлечению финансовых показателей из публичной российской
отчётности. Твоя задача — вернуть структурированный JSON по строгой
JSON-схеме, описывающий показатели по каждому отчётному периоду из
документа.

Правила:
1. Возвращай только то, что прямо подтверждено текстом документа.
   Не выдумывай числа, не округляй и не пересчитывай суммы.
2. Если показатель не найден — value=null, source_quote=null.
3. Для каждого непустого показателя обязательно укажи source_quote —
   точную короткую цитату из документа (≤ 250 символов).
4. Не нормализуй значения сам: возвращай их как в документе и укажи
   единицу измерения через поле "unit" ("ones" / "thousands" /
   "millions" / "billions"). Конвертацию выполнит пайплайн.
5. Возвращай по одному элементу массива "extractions" на каждый
   отчётный период, найденный в документе. period_type — один из
   Q1, Q2, Q3, Q4, H1, H2, 9M, FY.
6. reporting_standard — IFRS, если документ маркирован как МСФО /
   IFRS / Consolidated, иначе RSBU.
7. Возвращай только JSON — без пояснительного текста и Markdown.
"""


def build_system_prompt(metrics_config: MetricsConfig) -> str:
    """Stable system prompt, deterministic from the metrics catalogue."""
    lines: list[str] = [_STATIC_PREAMBLE.rstrip(), "", "Перечень показателей:"]
    for spec in metrics_config.metrics:
        ifrs = ", ".join(spec.synonyms_ifrs) if spec.synonyms_ifrs else "—"
        rsbu = ", ".join(spec.synonyms_rsbu) if spec.synonyms_rsbu else "—"
        formula = f" (формула: {spec.formula})" if spec.formula else ""
        lines.append(
            f"- {spec.canonical_name} — целевая валюта {spec.currency},"
            f" базовая единица {spec.unit}{formula}\n"
            f"  Синонимы IFRS: {ifrs}\n"
            f"  Синонимы РСБУ: {rsbu}"
        )
    priority = " > ".join(metrics_config.reporting_priority)
    lines.append("")
    lines.append(
        f"Приоритет стандартов отчётности при выборе документа: {priority}."
    )
    return "\n".join(lines)
