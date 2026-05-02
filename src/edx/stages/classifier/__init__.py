"""Classifier stage: detect reporting standard, report form, per-page kind."""

from edx.stages.classifier.factory import build_classifier_service
from edx.stages.classifier.heuristics import (
    ReportForm,
    detect_report_form,
    detect_reporting_standard,
)
from edx.stages.classifier.pdf_inspector import (
    PageClassification,
    classify_pages,
    count_pages,
    extract_first_pages_text,
)
from edx.stages.classifier.service import ClassifierService, ClassifyOutcome

__all__ = [
    "ClassifierService",
    "ClassifyOutcome",
    "PageClassification",
    "ReportForm",
    "build_classifier_service",
    "classify_pages",
    "count_pages",
    "detect_report_form",
    "detect_reporting_standard",
    "extract_first_pages_text",
]
