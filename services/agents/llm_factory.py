"""Build configured structured LLM runtime with provider fallback chain."""

from __future__ import annotations

import json

from services.agents.llm_runtime import (
    ConfiguredStructuredLLMRuntime,
    FallbackChainStructuredLLMRuntime,
    OpenAICompatibleStructuredLLMRuntime,
    StructuredLLMRuntime,
    UnavailableLLMRuntime,
    discover_github_models_free_models,
    discover_openrouter_free_models,
    parse_model_override,
)
from shared.config import settings

_OPENROUTER_SEED_MODELS = [
    "meta-llama/llama-3.1-8b-instruct:free",
    "google/gemma-3-27b-it:free",
]
_GITHUB_SEED_MODELS = [
    "openai/gpt-4.1-nano",
    "meta/Meta-Llama-3.1-8B-Instruct",
]


def _resolve_free_models(*, override: str, discover, seeds: list[str]) -> list[str]:  # noqa: ANN001
    configured = parse_model_override(override)
    if configured:
        return configured
    if settings.llm_use_catalog_seeds_only:
        return list(seeds)
    return discover()


def build_configured_llm_runtime() -> StructuredLLMRuntime:
    """Anthropic first, then OpenRouter free models, then GitHub Models free models."""
    runtimes: list[StructuredLLMRuntime] = []
    if settings.claude_api_key:
        runtimes.append(
            ConfiguredStructuredLLMRuntime(
                anthropic_api_key=settings.claude_api_key,
                default_model=settings.claude_model,
                anthropic_base_url=settings.anthropic_api_base_url,
                provider_by_agent=json.loads(settings.agent_llm_provider_map or "{}"),
                model_by_agent=json.loads(settings.agent_llm_model_map or "{}"),
            )
        )
    if settings.openrouter_api_key:
        models = _resolve_free_models(
            override=settings.openrouter_free_models,
            discover=lambda: discover_openrouter_free_models(
                api_key=settings.openrouter_api_key,
                cache_seconds=settings.llm_free_model_catalog_cache_seconds,
            ),
            seeds=_OPENROUTER_SEED_MODELS,
        )
        runtimes.extend(
            OpenAICompatibleStructuredLLMRuntime(
                api_key=settings.openrouter_api_key,
                model=model,
                base_url="https://openrouter.ai/api/v1",
                provider_label="openrouter",
            )
            for model in models
        )
    if settings.github_models_token:
        models = _resolve_free_models(
            override=settings.github_models_free_models,
            discover=lambda: discover_github_models_free_models(
                token=settings.github_models_token,
                cache_seconds=settings.llm_free_model_catalog_cache_seconds,
            ),
            seeds=_GITHUB_SEED_MODELS,
        )
        runtimes.extend(
            OpenAICompatibleStructuredLLMRuntime(
                api_key=settings.github_models_token,
                model=model,
                base_url="https://models.inference.ai.azure.com",
                provider_label="github_models",
            )
            for model in models
        )
    if not runtimes:
        return UnavailableLLMRuntime()
    if len(runtimes) == 1:
        return runtimes[0]
    return FallbackChainStructuredLLMRuntime(runtimes)
