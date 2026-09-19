# Adding an MCP tool

MCP lets an external client — an IDE, a desktop assistant, another agent — call
this system's capabilities directly. Add your own tools by implementing the
tool interface and registering them with the agent.

---

## Shipped tools

| Tool | Returns |
|---|---|
| `query_knowledge_base` | An answer from the full pipeline, plus citations |
| `list_domains` | The indexed knowledge domains |
| `search_documents` | Raw chunks, no generation |
| `get_domain_info` | Whether a domain exists and is loaded |
| `summarize_domain` | *(example)* A sampled description of a domain |
| `check_order_status` | *(example)* A lookup against a synthetic fixture |

Two levels of abstraction are exposed on purpose. A client with no model of its
own wants `query_knowledge_base`; a client that has one often prefers
`search_documents` so it can reason over the source material itself.

---

## The interface

A tool is a function decorated with `@mcp_tool`:

```python
from src.mcp_server.registry import mcp_tool


@mcp_tool("check_deployment", "Look up the deployed version of a service.")
def check_deployment(service: str, environment: str = "production") -> dict:
    """Return the deployed version and health of a service.

    Args:
        service: The service name to look up.
        environment: Which environment to check. Defaults to production.
    """
    return {"service": service, "environment": environment, "version": "1.4.2"}
```

Four things make a tool usable by a model:

- **Type annotations** — FastMCP derives the advertised schema from them. An
  unannotated parameter is an unusable one.
- **A docstring with `Args:`** — this is how the model learns what to pass.
  Treat it as the tool's user interface, not as internal documentation.
- **A structured return** — a dict or a list. A model can branch on a dict; it
  can only guess at a sentence.
- **Structured errors, not exceptions** — return `{"error": ...}` with enough
  context to retry. A raised exception tells the caller only that something
  failed.

Registration happens at import. `src/mcp_server/server.py` imports `tools` and
`examples`; add your module there, or import it from one of them.

---

## Tools that need retrieval

Take `context: ToolContext` as the first parameter. The registry injects it and
hides it from the schema the client sees, so the model never knows it exists.

```python
from src.mcp_server.registry import ToolContext, mcp_tool


@mcp_tool("compare_domains", "Compare how two knowledge domains describe a topic.")
def compare_domains(context: ToolContext, topic: str, first: str, second: str) -> dict:
    """Retrieve passages about a topic from two domains side by side.

    Args:
        topic: What to compare, e.g. "retry behaviour".
        first: The first knowledge domain.
        second: The second knowledge domain.
    """

    def passages(domain: str) -> list[str]:
        try:
            return [d.page_content for d in context.store.retrieve(topic, domains=[domain], k=3)]
        except ValueError:
            return []

    return {"topic": topic, first: passages(first), second: passages(second)}
```

`ToolContext` exposes:

| Attribute | What it is |
|---|---|
| `context.store` | The vector store. Built on first access. |
| `context.engine` | The RAG engine, for a full pipeline run. Built on first access. |

Both are lazy so the MCP handshake stays instant — a client expects it in
milliseconds, and building a vector store can take seconds.

---

## Tools that call an external system

`examples.check_order_status` is the template. It reads a local JSON fixture so
the demo works offline; replace that read with your own call and keep the
contract:

```python
import requests
from src.mcp_server.registry import mcp_tool


@mcp_tool("get_ticket", "Fetch a ticket from the issue tracker.")
def get_ticket(ticket_id: str) -> dict:
    """Fetch a single ticket from the issue tracker.

    Args:
        ticket_id: The ticket identifier, e.g. "PROJ-1234".
    """
    import os

    base = os.environ.get("TRACKER_BASE_URL")
    if not base:
        return {"error": "TRACKER_BASE_URL is not configured"}

    try:
        resp = requests.get(
            f"{base}/api/tickets/{ticket_id}",
            headers={"Authorization": f"Bearer {os.environ['TRACKER_TOKEN']}"},
            timeout=10,
        )
    except requests.RequestException as exc:
        return {"error": f"Tracker unreachable ({type(exc).__name__})", "ticket_id": ticket_id}

    if resp.status_code == 404:
        return {"error": "Ticket not found", "ticket_id": ticket_id}
    resp.raise_for_status()
    return resp.json()
```

Three rules, all of which matter more than usual because the caller is a model:

1. **Credentials come from the environment.** Never a literal, never a default.
2. **Set a timeout.** A hanging tool hangs the client's whole turn.
3. **Do not return raw upstream errors.** They leak hostnames and internal
   detail into a transcript you do not control.

---

## Running and connecting

```bash
python mcp_app.py                               # stdio
python mcp_app.py --transport sse --port 8100   # HTTP
```

`.vscode/mcp.json` wires the stdio server into an MCP-aware editor. For other
clients, point them at the same command:

```json
{
  "mcpServers": {
    "knowledge-rag": {
      "command": "/path/to/repo/.venv/bin/python",
      "args": ["/path/to/repo/mcp_app.py"],
      "env": { "DEMO_MODE": "true" }
    }
  }
}
```

---

## Testing

Test the function directly — it is ordinary Python, and the registry is not in
the way:

```python
def test_compare_domains(store, llm):
    from src.mcp_server.registry import ToolContext
    from src.rag.engine import RAGEngine

    context = ToolContext()
    context._store = store
    context._engine = RAGEngine(store=store, llm=llm)

    result = compare_domains(context, "retries", "order_service", "payment_service")
    assert result["topic"] == "retries"
```

Also assert that the tool is registered and described, since a tool a model
cannot discover is not a tool:

```python
from src.mcp_server.registry import registered_tools


def test_is_registered():
    specs = {s.name: s for s in registered_tools()}
    assert "compare_domains" in specs
    assert specs["compare_domains"].description
```

`tests/test_mcp_tools.py` covers both patterns.

---

## Design notes

- **Name tools for what they do, not for what they wrap.** `search_documents`
  travels better than `milvus_similarity_query`.
- **Keep them narrow.** A tool with eight parameters and a mode flag is
  several tools that a model will call wrongly.
- **Return provenance.** When a tool returns facts, say where they came from;
  the client usually needs to cite them.
- **Assume the caller is a model.** Every string you return may end up in
  someone's answer, so return data rather than prose, and never return content
  that reads like an instruction.
