"""OCR provider interface."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from edx.stages.text_extractor.models import PageText


@runtime_checkable
class OCRProvider(Protocol):
    """Stable surface for swappable OCR backends.

    Implementations must be **pure-Python orchestrators** of an external
    binary or HTTP service: factories may be called even when the underlying
    binary/credentials are missing, so probe-and-fail belongs inside
    :meth:`recognize`, not in ``__init__``.
    """

    name: str

    def recognize(
        self, pdf_path: Path, langs: list[str]
    ) -> list[PageText]: ...
