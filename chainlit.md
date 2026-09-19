# Knowledge Assistant

Ask questions in natural language about the documentation indexed in this
system. Answers are grounded in retrieved passages, and each answer lists the
source files it was built from.

## How to use it

1. **Pick a knowledge domain** from the settings panel (⚙️) to narrow the
   search, or leave it on *All domains* to search everything.
2. **Ask a question** in plain language.
3. **Follow the sources.** Every answer cites the documents behind it — check
   them for anything that matters.

Selecting a domain also surfaces a few suggested questions for it.

## What it is good at

- Locating a specific detail buried in a long document.
- Summarising how something works across several files in one domain.
- Answering "where is this configured / what happens when X fails" questions.

## What to watch for

- Answers are only as good as the indexed corpus. If something is not
  documented, the assistant should say so — but a model can still be confidently
  wrong. Check the cited sources.
- The assistant has no memory of systems outside its corpus, and no access to
  live data.
- In demo mode no model runs at all: the assistant returns the retrieved
  passages verbatim, clearly labelled.

## Bringing your own documents

Drop a folder of Markdown, text or PDF files into `data/`, then re-index. Each
folder becomes its own searchable knowledge domain. See `docs/extending.md`.
