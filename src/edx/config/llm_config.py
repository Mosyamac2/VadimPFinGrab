"""LLM provider configuration: primary Anthropic + OpenRouter fallback (ТЗ §17)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AnthropicProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    model: str = Field(default="claude-sonnet-4-6", min_length=1)
    base_url: str | None = None
    enable_pdf_input: bool = True
    enable_prompt_caching: bool = True


class OpenRouterProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    model: str = Field(default="anthropic/claude-sonnet-4.6", min_length=1)
    base_url: str = "https://openrouter.ai/api/v1"


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: AnthropicProviderConfig = Field(default_factory=AnthropicProviderConfig)
    fallback: OpenRouterProviderConfig = Field(default_factory=OpenRouterProviderConfig)
    max_tokens: int = Field(default=4096, gt=0)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    request_timeout_s: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    retry_min_wait_s: float = Field(default=0.5, ge=0)
    retry_max_wait_s: float = Field(default=10.0, gt=0)
    concurrency: int = Field(default=4, ge=1)
    # Persist successful responses keyed by SHA-256(system+user_text+pdf+schema)
    # under data/processed/_llm_cache/. Disable for development if you want
    # every call to hit the live provider.
    cache_enabled: bool = True
