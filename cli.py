#!/usr/bin/env python3
"""Command-line entry point.

Runs the RAG engine in-process — no HTTP service, no UI — which makes it the
quickest way to try a configuration change or to index a corpus.

    python cli.py index                     # build the index from data/
    python cli.py ask "how do refunds work?"
    python cli.py ask "..." --domains payment_service
    python cli.py chat                      # interactive
    python cli.py domains
"""

from __future__ import annotations

import argparse
import logging
import sys

from src.config.settings import settings
from src.rag.engine import RAGEngine
from src.vectorstore.factory import build_knowledge_base, discover_domains, index_domains

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("cli")


def _banner() -> None:
    # The offline stubs ignore the configured model name, so don't print one
    # for them — it would suggest inference is happening when none is.
    llm = (
        settings.llm.provider
        if settings.llm.provider == "echo"
        else f"{settings.llm.provider} / {settings.llm.model}"
    )
    embed = (
        settings.embeddings.provider
        if settings.embeddings.provider == "hashing"
        else f"{settings.embeddings.provider} / {settings.embeddings.model}"
    )
    mode = "DEMO (offline stubs)" if settings.demo_mode else "configured providers"

    print(f"Multi-Domain RAG Agent — {mode}")
    print(f"  pipeline     : {settings.pipeline}")
    print(f"  llm          : {llm}")
    print(f"  embeddings   : {embed}")
    print(f"  vector store : {settings.vector_store.backend}")
    print()


def cmd_index(args: argparse.Namespace) -> int:
    store = build_knowledge_base(init_mode="retrieve")
    targets = args.domains or discover_domains(settings.data_dir)
    if not targets:
        print(f"No knowledge domains found under '{settings.data_dir}'.")
        return 1
    index_domains(store, targets)
    print(f"Indexed: {', '.join(store.loaded_domains()) or 'nothing'}")
    return 0


def cmd_domains(args: argparse.Namespace) -> int:
    store = build_knowledge_base()
    domains = store.loaded_domains()
    if not domains:
        print("No knowledge domains indexed. Run: python cli.py index")
        return 1
    for domain in domains:
        print(f"  - {domain}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    store = build_knowledge_base()
    engine = RAGEngine(store=store)
    result = engine.run({"question": args.question, "domains": args.domains})

    print()
    print(result.get("answer", "(no answer)"))
    citations = (result.get("metadata") or {}).get("citations", [])
    if citations:
        print("\nSources:")
        for c in citations:
            print(f"  - {c.get('domain')}/{c.get('source')}")
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    store = build_knowledge_base()
    engine = RAGEngine(store=store)
    print("Interactive mode. Type 'quit' to exit.\n")
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question.lower() in {"quit", "exit", "q"}:
            return 0
        if not question:
            continue
        try:
            print(f"\n{engine.ask(question, domains=args.domains)}\n")
        except Exception as exc:
            print(f"Error: {exc}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-Domain RAG Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="Index knowledge domains from the corpus directory")
    p_index.add_argument("--domains", nargs="*", help="Limit to these domains")
    p_index.set_defaults(func=cmd_index)

    p_domains = sub.add_parser("domains", help="List indexed knowledge domains")
    p_domains.set_defaults(func=cmd_domains)

    p_ask = sub.add_parser("ask", help="Ask a single question")
    p_ask.add_argument("question")
    p_ask.add_argument("--domains", nargs="*", help="Restrict retrieval to these domains")
    p_ask.set_defaults(func=cmd_ask)

    p_chat = sub.add_parser("chat", help="Interactive question loop")
    p_chat.add_argument("--domains", nargs="*", help="Restrict retrieval to these domains")
    p_chat.set_defaults(func=cmd_chat)

    args = parser.parse_args()
    _banner()
    try:
        return args.func(args)
    except Exception as exc:
        logger.error("%s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
