"""Offline chat model used by demo mode and by the test suite.

This is a *stub*, not a language model. It performs no inference and no network
I/O. Given a prompt it echoes back the most relevant-looking lines of the
context it was handed, which is enough to exercise the full request path —
retrieval, graph execution, streaming, API serialisation, UI rendering —
without any provider credentials.

Anything that actually needs generated language should configure a real
provider; see ``docs/configuration.md``.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

_CONTEXT_BLOCK = re.compile(r"Context:\s*(.*?)\s*Question:", re.DOTALL)
_ATTRIBUTION = re.compile(r"^\[\d+\]\s*\(")
_STOPWORDS = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "to",
    "of",
    "and",
    "or",
    "in",
    "on",
    "for",
    "with",
    "how",
    "what",
    "why",
    "when",
    "does",
    "do",
    "it",
    "this",
    "that",
    "from",
    "by",
    "as",
    "at",
    "can",
    "which",
    "you",
    "your",
}


def _keywords(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS and len(w) > 2]


class EchoChatModel(BaseChatModel):
    """Deterministic, extractive stand-in for a chat model."""

    max_sentences: int = 6

    @property
    def _llm_type(self) -> str:
        return "echo"

    @property
    def model_name(self) -> str:  # surfaced by the API's /v1/models route
        return "echo-demo"

    # -- core ---------------------------------------------------------------

    def _compose(self, prompt: str) -> str:
        match = _CONTEXT_BLOCK.search(prompt)
        context = match.group(1) if match else ""
        question = prompt.rsplit("Question:", 1)[-1].strip() if "Question:" in prompt else prompt

        if not context.strip():
            return (
                "**Demo mode** — no context was retrieved for this question, so there is "
                "nothing to ground an answer in. Index a knowledge domain first, or "
                "configure a real LLM provider."
            )

        wanted = set(_keywords(question))
        sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+|\n+", context)
            # Skip the "[1] (domain · file.md)" attribution lines the context
            # formatter inserts, and Markdown headings — neither is content.
            if len(s.strip()) > 40 and not _ATTRIBUTION.match(s.strip()) and not s.lstrip().startswith("#")
        ]
        scored = sorted(
            ((len(wanted & set(_keywords(s))), -i, s) for i, s in enumerate(sentences)),
            reverse=True,
        )
        picked = [s for score, _, s in scored[: self.max_sentences] if score > 0]
        if not picked:
            picked = sentences[: self.max_sentences]

        body = "\n\n".join(f"- {s}" for s in picked)
        return (
            "**Demo mode (echo provider — no model inference).** "
            "The passages below are the retrieved context most relevant to your question.\n\n"
            f"{body}\n\n"
            "_Set `DEMO_MODE=false` and configure `LLM_PROVIDER` to get a generated answer._"
        )

    @staticmethod
    def _prompt_from(messages: list[BaseMessage]) -> str:
        return "\n".join(str(m.content) for m in messages)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        text = self._compose(self._prompt_from(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        text = self._compose(self._prompt_from(messages))
        for i in range(0, len(text), 64):
            yield ChatGenerationChunk(message=AIMessageChunk(content=text[i : i + 64]))

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._stream(messages, stop=stop, **kwargs):
            yield chunk
