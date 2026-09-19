"""Prompt construction.

Kept in one module so prompts can be reviewed, diffed and overridden without
reading the graph code. ``ANSWER_PROMPT`` is deliberately plain: the interesting
behaviour lives in retrieval and in the grounding instruction, not in prompt
decoration.

The grounding instruction ("say when the context does not cover it") is the
main defence against the failure mode that matters for a documentation
assistant — a confident answer assembled from the model's priors rather than
from the indexed corpus.
"""

from __future__ import annotations

from langchain_core.documents import Document

ANSWER_PROMPT = """You are a documentation assistant. Answer the question using \
only the context below.

Rules:
- Ground every claim in the context. Do not add information that is not there.
- If the context does not contain the answer, say so plainly and state what is missing.
- Prefer concrete detail from the context over general knowledge.
- When several passages disagree, note the disagreement instead of picking one.

Context:
{context}

Question: {question}

Answer:"""

AGENT_SYSTEM_PROMPT = """You are a documentation research agent.

Use the `search_knowledge_base` tool to look things up in the indexed corpus. \
Search more than once if the first result is incomplete or the question has \
several parts. Ground your answer in what the tool returns, and say so when the \
corpus does not cover something. Return only the final answer."""


def format_context(documents: list[Document], max_chars: int = 12000) -> str:
    """Render retrieved chunks into a numbered, attributed context block.

    Numbering and per-chunk attribution let the model refer to a specific
    passage, and let a reader check the answer against its source. The budget
    is enforced here rather than by the caller so that no prompt can silently
    exceed a model's context window.
    """
    if not documents:
        return ""

    blocks: list[str] = []
    used = 0
    for i, doc in enumerate(documents, start=1):
        meta = doc.metadata or {}
        label = meta.get("source_file") or meta.get("file_path") or f"chunk {i}"
        domain = meta.get("retrieved_from") or meta.get("domain") or "unknown"
        block = f"[{i}] ({domain} · {label})\n{doc.page_content}"
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def build_answer_prompt(question: str, documents: list[Document], max_chars: int = 12000) -> str:
    return ANSWER_PROMPT.format(context=format_context(documents, max_chars=max_chars), question=question)
