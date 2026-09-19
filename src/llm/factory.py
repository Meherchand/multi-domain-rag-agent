"""Chat-model factory.

Extension point: register a callable under a provider name and it becomes
selectable via ``LLM_PROVIDER``. See ``docs/extending.md``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel

from src.config.settings import LLMSettings, settings

logger = logging.getLogger(__name__)

LLMBuilder = Callable[[LLMSettings], BaseChatModel]

_REGISTRY: dict[str, LLMBuilder] = {}


def register_llm(name: str, builder: LLMBuilder) -> None:
    """Register a chat-model builder under ``name``."""
    _REGISTRY[name.lower()] = builder


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def _build_openai(cfg: LLMSettings) -> BaseChatModel:
    """Any OpenAI-compatible chat-completions endpoint.

    ``base_url`` is what distinguishes the hosted API from a self-hosted
    gateway; leave it unset for api.openai.com.
    """
    from langchain_openai import ChatOpenAI

    if not cfg.api_key:
        raise ValueError(
            "LLM_API_KEY is not set. Set it, or run with DEMO_MODE=true to use the offline echo provider."
        )

    kwargs = {
        "api_key": cfg.api_key,
        "model": cfg.model,
        "temperature": cfg.temperature,
        "top_p": cfg.top_p,
        "max_tokens": cfg.max_tokens,
        "timeout": cfg.timeout,
    }
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return ChatOpenAI(**kwargs)


def _build_echo(cfg: LLMSettings) -> BaseChatModel:
    from src.llm.echo import EchoChatModel

    return EchoChatModel()


register_llm("openai", _build_openai)
register_llm("echo", _build_echo)


def get_llm(cfg: LLMSettings | None = None) -> BaseChatModel:
    """Build the configured chat model."""
    cfg = cfg or settings.llm
    builder = _REGISTRY.get(cfg.provider)
    if builder is None:
        raise ValueError(
            f"Unknown LLM_PROVIDER '{cfg.provider}'. Available: {', '.join(available_providers())}"
        )
    logger.info("Using LLM provider '%s' (model=%s)", cfg.provider, cfg.model)
    return builder(cfg)
