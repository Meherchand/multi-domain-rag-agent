"""Chainlit chat UI.

A thin client over the HTTP API — it holds no retrieval logic of its own, which
keeps the API the single place where answering happens and lets the UI be
swapped or removed.

Optional features degrade rather than fail:

* **Auth** is off unless ``OAUTH_GOOGLE_CLIENT_ID`` is set. Without it the app
  runs anonymously, which is what you want locally.
* **Persistence** (chat history, share links) is off unless
  ``CHAT_PERSISTENCE_ENABLED=true``. Without it, chats are ephemeral.

Domain labels, groupings and suggested questions come from
``data/domains.json``; nothing about the corpus is hardcoded here.
"""

from __future__ import annotations

import html
import json
import logging
import os

import aiohttp
import chainlit as cl
from chainlit.input_widget import Select
from chainlit.server import app as chainlit_server
from starlette.responses import HTMLResponse

from src.config.domains import catalog
from src.config.settings import settings
from src.db.init_db import initialize_database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_BASE = settings.api.base_url
API_KEY = settings.api.api_key
PUBLIC_BASE_URL = settings.api.public_base_url
APP_NAME = os.environ.get("APP_NAME", "Knowledge Assistant")

PERSISTENCE_READY = initialize_database()
OAUTH_ENABLED = bool(os.environ.get("OAUTH_GOOGLE_CLIENT_ID"))


# --------------------------------------------------------------------------
# Optional integrations
# --------------------------------------------------------------------------

if PERSISTENCE_READY:

    @cl.data_layer
    def make_data_layer():
        from chainlit.data.sql_alchemy import SQLAlchemyDataLayer

        return SQLAlchemyDataLayer(conninfo=settings.database.async_dsn)


if OAUTH_ENABLED:

    @cl.oauth_callback
    def oauth_callback(
        provider_id: str,
        token: str,
        raw_user_data: dict[str, str],
        default_user: cl.User,
    ) -> cl.User | None:
        """Accept any account the configured provider authenticates.

        Restricting *which* accounts may sign in is deployment policy, not
        something this reference implementation should decide — add the check
        here if you need one.
        """
        default_user.metadata.update({"provider": provider_id})
        return default_user


# --------------------------------------------------------------------------
# API client
# --------------------------------------------------------------------------


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    return headers


async def fetch_domains() -> list[str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_BASE}/domains", headers=_headers(), timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    logger.warning("Domain listing returned HTTP %s", resp.status)
                    return []
                return (await resp.json()).get("domains", [])
    except Exception as exc:
        logger.warning("Could not reach the API to list domains: %s", type(exc).__name__)
        return []


# --------------------------------------------------------------------------
# Rendering helpers
# --------------------------------------------------------------------------


def _domain_widget(available: list[str], initial: str | None = None) -> cl.ChatSettings:
    """Build the domain picker, grouped per data/domains.json."""
    items: dict[str, str] = {"All domains": "__all__"}
    for group in catalog.grouped(available):
        for domain in group["domains"]:
            items[f"{group['name']} · {catalog.label(domain)}"] = domain

    return cl.ChatSettings(
        [
            Select(
                id="selected_domain",
                label="Knowledge domain",
                items=items,
                initial_value=initial if initial in items.values() else "__all__",
            )
        ]
    )


def _selected(session_value) -> list[str] | None:
    """Translate the picker value into the API's `domains` field."""
    if not session_value or session_value == "__all__":
        return None
    return [session_value]


async def _suggest_faqs(domain: str) -> None:
    faqs = catalog.faqs(domain)
    if not faqs:
        return
    actions = [
        cl.Action(name="ask_faq", payload={"question": q}, label=q, tooltip="Ask this question")
        for q in faqs[:5]
    ]
    await cl.Message(content=f"**Suggested questions — {catalog.label(domain)}**", actions=actions).send()


# --------------------------------------------------------------------------
# Question handling
# --------------------------------------------------------------------------


async def answer(question: str) -> None:
    if not question:
        return

    domains = _selected(cl.user_session.get("selected_domain"))
    scope = ", ".join(catalog.label(d) for d in domains) if domains else "all knowledge domains"

    msg = cl.Message(content="")
    await msg.send()
    await msg.stream_token(f"_Searching {scope}…_\n\n")

    body = {"question": question, "domains": domains}
    collected: list[str] = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/query/stream",
                headers=_headers(),
                json=body,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as resp:
                if resp.status != 200:
                    msg.content = f"The API returned HTTP {resp.status}. Check that it is running."
                    await msg.update()
                    return

                msg.content = ""
                await msg.update()

                async for line in resp.content:
                    decoded = line.decode("utf-8").strip()
                    if not decoded:
                        continue
                    try:
                        chunk = json.loads(decoded)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        collected.append(f"\n\n_{chunk['error']}_")
                        break
                    if chunk.get("done"):
                        break
                    token = chunk.get("chunk", "")
                    collected.append(token)
                    await msg.stream_token(token)
    except Exception as exc:
        msg.content = f"Could not reach the API ({type(exc).__name__}). Is it running at {API_BASE}?"
        await msg.update()
        return

    final = "".join(collected).strip()
    msg.content = final or "No answer was produced."

    if PERSISTENCE_READY and final:
        msg.actions = [
            cl.Action(
                name="share_answer",
                payload={"question": question, "answer": final, "domains": domains or []},
                label="Share",
                tooltip="Create a link anyone can open to read this answer",
                icon="share-2",
            )
        ]
    await msg.update()


@cl.action_callback("ask_faq")
async def on_faq(action: cl.Action) -> None:
    await answer((action.payload or {}).get("question", ""))


@cl.action_callback("share_answer")
async def on_share(action: cl.Action) -> None:
    payload = action.payload or {}
    if not payload.get("answer"):
        return

    from src.db.share_store import create_share

    user = cl.user_session.get("user")
    try:
        token = await create_share(
            payload.get("question", ""),
            payload["answer"],
            payload.get("domains") or [],
            user.identifier if user else None,
        )
    except Exception as exc:
        logger.error("Could not create a share link: %s", type(exc).__name__)
        await cl.Message(content="Could not create a share link.").send()
        return

    url = f"{PUBLIC_BASE_URL}/share/{token}"
    await cl.Message(
        content=f"Shareable link — anyone with it can read this answer:\n\n[{url}]({url})"
    ).send()


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


@cl.on_chat_start
async def on_chat_start() -> None:
    domains = await fetch_domains()
    cl.user_session.set("available_domains", domains)
    cl.user_session.set("selected_domain", "__all__")

    if not domains:
        await cl.Message(
            content=(
                f"No knowledge domains are indexed yet, or the API at `{API_BASE}` is "
                "unreachable.\n\nStart the API, then index the demo corpus:\n\n"
                "```bash\nmake demo\n```"
            )
        ).send()
        return

    await _domain_widget(domains).send()
    await cl.Message(
        content=(
            f"### {APP_NAME}\n\n"
            f"Ask a question about the indexed documentation. "
            f"{len(domains)} knowledge domain(s) are available — pick one from the settings "
            "panel to narrow the search, or leave it on **All domains**.\n\n"
            + "\n".join(
                f"- **{catalog.label(d)}** — {catalog.description(d) or 'indexed corpus'}" for d in domains
            )
        )
    ).send()


@cl.on_settings_update
async def on_settings_update(new_settings) -> None:
    domain = new_settings.get("selected_domain")
    if not domain or domain == cl.user_session.get("selected_domain"):
        return

    cl.user_session.set("selected_domain", domain)
    if domain == "__all__":
        await cl.Message(content="Searching **all knowledge domains**.").send()
        return

    await cl.Message(content=f"Scoped to **{catalog.label(domain)}**.").send()
    await _suggest_faqs(domain)


@cl.on_message
async def on_message(message: cl.Message) -> None:
    await answer(message.content.strip())


@cl.on_chat_resume
async def on_chat_resume(thread) -> None:
    domains = await fetch_domains()
    cl.user_session.set("available_domains", domains)
    saved = ((thread.get("metadata") or {}).get("chat_settings") or {}).get("selected_domain")
    cl.user_session.set("selected_domain", saved or "__all__")
    if domains:
        await _domain_widget(domains, initial=saved).send()


# --------------------------------------------------------------------------
# Share page
# --------------------------------------------------------------------------

_SHARE_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="robots" content="noindex" />
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"></script>
<style>
  :root {{ --accent:#2563eb; --text:#0f172a; --muted:#64748b; --border:#e2e8f0; --bg:#f8fafc; --card:#fff; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --text:#f1f5f9; --muted:#94a3b8; --border:#334155; --bg:#0f172a; --card:#1e293b; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text); line-height:1.6;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
  .container {{ max-width:820px; margin:0 auto; padding:32px 20px 80px; }}
  .brand {{ font-weight:600; font-size:14px; color:var(--muted); margin-bottom:24px; }}
  h1 {{ font-size:26px; font-weight:600; letter-spacing:-0.02em; margin:0 0 14px; }}
  .meta {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:24px; font-size:13px; color:var(--muted); }}
  .chip {{ background:rgba(37,99,235,0.08); color:var(--accent); border:1px solid rgba(37,99,235,0.2);
    padding:3px 10px; border-radius:999px; font-weight:500; }}
  .answer {{ background:var(--card); border:1px solid var(--border); border-radius:14px;
    padding:24px 26px; overflow-wrap:anywhere; }}
  .answer pre {{ background:rgba(127,127,127,0.12); padding:14px 16px; border-radius:10px; overflow:auto; }}
  .answer code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:0.9em; }}
  .answer pre code {{ background:transparent; padding:0; }}
  .answer table {{ border-collapse:collapse; width:100%; }}
  .answer th, .answer td {{ border:1px solid var(--border); padding:8px 10px; text-align:left; }}
  .answer a {{ color:var(--accent); }}
  .footer {{ margin-top:32px; text-align:center; font-size:12px; color:var(--muted); }}
</style>
</head>
<body>
  <div class="container">
    <div class="brand">{app_name}</div>
    <h1 id="question"></h1>
    <div class="meta">{chips}</div>
    <article class="answer" id="answer"></article>
    <div class="footer">Generated answers can be wrong. Check anything important against the source.</div>
  </div>
  <script>
    // The payload is injected as JSON and rendered client-side through
    // DOMPurify: the answer is model output and is never trusted as markup.
    const data = {payload};
    document.getElementById("question").textContent = data.question;
    document.getElementById("answer").innerHTML =
      DOMPurify.sanitize(marked.parse(data.answer || ""));
  </script>
</body>
</html>"""

_NOT_FOUND = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Link not found</title><style>
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f8fafc;color:#0f172a;}
.box{text-align:center;padding:40px;} h1{font-size:22px;margin:0 0 8px;} p{color:#64748b;margin:0;}
</style></head><body><div class="box"><h1>Shared answer not found</h1>
<p>This link is invalid or the answer is no longer available.</p></div></body></html>"""


async def serve_share(token: str):
    if not PERSISTENCE_READY:
        return HTMLResponse(content=_NOT_FOUND, status_code=404)

    from src.db.share_store import get_share

    try:
        record = await get_share(token)
    except Exception as exc:
        logger.error("Could not load a shared answer: %s", type(exc).__name__)
        record = None
    if not record:
        return HTMLResponse(content=_NOT_FOUND, status_code=404)

    question = record.get("question") or "Shared answer"
    chips = "".join(
        f'<span class="chip">{html.escape(catalog.label(str(d)))}</span>' for d in record.get("domains") or []
    )
    page = _SHARE_PAGE.format(
        title=html.escape(question[:80] + ("…" if len(question) > 80 else "")),
        app_name=html.escape(APP_NAME),
        chips=chips,
        payload=json.dumps({"question": question, "answer": record.get("answer", "")}),
    )
    return HTMLResponse(content=page)


chainlit_server.add_api_route("/share/{token}", serve_share, methods=["GET"])
# Chainlit registers a catch-all SPA route; move this one ahead of it.
chainlit_server.router.routes.insert(0, chainlit_server.router.routes.pop())
