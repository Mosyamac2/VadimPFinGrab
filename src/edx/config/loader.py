"""YAML loader for the six configuration files plus ``.env`` secrets.

No caching — re-read on every invocation per ТЗ §9.2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from edx.config.app_config import AppConfig
from edx.config.event_types_config import EventTypesConfig
from edx.config.llm_config import LLMConfig
from edx.config.metrics_config import MetricsConfig
from edx.config.ocr_config import OCRConfig
from edx.config.secrets import Secrets
from edx.config.settings import AppSettings
from edx.config.tickers_config import TickersConfig

CONFIG_FILES: Final[dict[str, str]] = {
    "app": "app.yaml",
    "tickers": "tickers.yaml",
    "metrics": "metrics.yaml",
    "event_types": "event_types.yaml",
    "llm": "llm.yaml",
    "ocr": "ocr.yaml",
}

M = TypeVar("M", bound=BaseModel)


class ConfigLoadError(RuntimeError):
    """Raised when a YAML file is missing, malformed, or fails Pydantic validation.

    Carries the offending file path and (when known) the dotted field path.
    """

    def __init__(
        self,
        file_path: Path,
        field_path: str | None,
        message: str,
    ) -> None:
        self.file_path = file_path
        self.field_path = field_path
        self.message = message
        if field_path:
            super().__init__(f"{file_path}: {field_path}: {message}")
        else:
            super().__init__(f"{file_path}: {message}")

    @classmethod
    def from_validation(
        cls, file_path: Path, exc: ValidationError
    ) -> ConfigLoadError:
        first = exc.errors()[0]
        loc = ".".join(str(part) for part in first.get("loc", ()))
        return cls(file_path, loc or None, str(first.get("msg", "validation error")))


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigLoadError(path, None, "config file not found")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigLoadError(path, None, f"cannot read file: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigLoadError(path, None, f"invalid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigLoadError(path, None, "top-level YAML must be a mapping")
    return data


def _validate(model_cls: type[M], data: dict[str, Any], path: Path) -> M:
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise ConfigLoadError.from_validation(path, exc) from exc


def load_all(
    config_dir: Path | str,
    *,
    env_file: Path | None = None,
) -> AppSettings:
    """Load and validate the full configuration tree.

    Always re-reads from disk (no caching).
    """
    cd = Path(config_dir)

    app_path = cd / CONFIG_FILES["app"]
    tickers_path = cd / CONFIG_FILES["tickers"]
    metrics_path = cd / CONFIG_FILES["metrics"]
    event_types_path = cd / CONFIG_FILES["event_types"]
    llm_path = cd / CONFIG_FILES["llm"]
    ocr_path = cd / CONFIG_FILES["ocr"]

    app = _validate(AppConfig, _read_yaml_mapping(app_path), app_path)
    tickers = _validate(TickersConfig, _read_yaml_mapping(tickers_path), tickers_path)
    metrics = _validate(MetricsConfig, _read_yaml_mapping(metrics_path), metrics_path)
    event_types = _validate(
        EventTypesConfig, _read_yaml_mapping(event_types_path), event_types_path
    )
    llm = _validate(LLMConfig, _read_yaml_mapping(llm_path), llm_path)
    ocr = _validate(OCRConfig, _read_yaml_mapping(ocr_path), ocr_path)

    secrets = Secrets.load(env_file=env_file)

    return AppSettings(
        app=app,
        tickers=tickers,
        metrics=metrics,
        event_types=event_types,
        llm=llm,
        ocr=ocr,
        secrets=secrets,
    )
