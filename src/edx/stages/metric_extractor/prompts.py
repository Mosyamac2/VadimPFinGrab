"""Build the LLM system prompt from a :class:`MetricsProfile`.

Patch 19 narrows the prompt by issuer profile (bank vs non-bank) AND by
source standard (IFRS vs RSBU vs ISSUER): metrics with
``only_in_sources`` that don't include the chosen source are dropped
from the prompt entirely (saves tokens, prevents the LLM from
hallucinating an EBITDA out of an RSBU document); ``aggregation_hint``
is injected only when the source is RSBU (the IFRS path already
publishes the aggregated form).
"""

from __future__ import annotations

from edx.config import MetricSpec, MetricsProfile, ReportingStandard

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


def build_system_prompt(
    profile: MetricsProfile, *, source_standard: ReportingStandard
) -> str:
    """Stable prompt for the (profile, source_standard) pair.

    Strips metrics whose ``only_in_sources`` excludes ``source_standard``
    and adds RSBU-only ``aggregation_hint`` notes underneath the metric
    list.
    """
    selected = _select_metrics(profile, source_standard)
    lines: list[str] = [_STATIC_PREAMBLE.rstrip(), "", "Перечень показателей:"]
    for canonical, spec in selected.items():
        synonyms = ", ".join(spec.synonyms) if spec.synonyms else "—"
        scale = (
            f" (типичные единицы: {', '.join(spec.scale_hints)})"
            if spec.scale_hints
            else ""
        )
        lines.append(
            f"- {canonical} — целевая валюта {spec.unit}{scale}\n"
            f"  Синонимы: {synonyms}"
        )

    rsbu_hints = [
        (name, spec.aggregation_hint)
        for name, spec in selected.items()
        if source_standard == "RSBU" and spec.aggregation_hint
    ]
    if rsbu_hints:
        lines.append("")
        lines.append("Подсказки по агрегации для РСБУ:")
        for name, hint in rsbu_hints:
            lines.append(f"- {name}: {hint}")

    priority = " > ".join(profile.reporting_priority)
    lines.append("")
    lines.append(
        f"Приоритет стандартов отчётности при выборе документа: {priority}."
    )
    return "\n".join(lines)


def _select_metrics(
    profile: MetricsProfile, source_standard: ReportingStandard
) -> dict[str, MetricSpec]:
    """Filter the profile's metric dict by ``only_in_sources``.

    Returns the same insertion order as the YAML so prompt caching stays
    stable across calls with the same (profile, source) pair.
    """
    out: dict[str, MetricSpec] = {}
    for name, spec in profile.metrics.items():
        if spec.only_in_sources and source_standard not in spec.only_in_sources:
            continue
        out[name] = spec
    return out
