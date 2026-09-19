"""Streamlit chat UI — an alternative front end over the same HTTP API.

Kept alongside the Chainlit app to demonstrate that the API is the real
interface: neither UI contains retrieval logic, and either can be deleted
without touching the rest of the system.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import requests
import streamlit as st

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("API_KEY", "")
APP_NAME = os.environ.get("APP_NAME", "Knowledge Assistant")

st.set_page_config(page_title=APP_NAME, page_icon="📚", layout="wide")


def headers() -> dict:
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["X-API-Key"] = API_KEY
    return h


@st.cache_data(ttl=30)
def api_healthy() -> bool:
    try:
        return requests.get(f"{API_BASE}/health", timeout=5).status_code == 200
    except requests.RequestException:
        return False


@st.cache_data(ttl=300)
def fetch_domains() -> list[str]:
    try:
        resp = requests.get(f"{API_BASE}/domains", headers=headers(), timeout=10)
        resp.raise_for_status()
        return resp.json().get("domains", [])
    except requests.RequestException:
        return []


def stream_answer(question: str, domains: list[str] | None, placeholder) -> str:
    body = {"question": question, "domains": domains}
    resp = requests.post(f"{API_BASE}/query/stream", json=body, headers=headers(), timeout=180, stream=True)
    resp.raise_for_status()

    answer = ""
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        if chunk.get("error"):
            answer += f"\n\n_{chunk['error']}_"
            break
        if chunk.get("done"):
            break
        answer += chunk.get("chunk", "")
        placeholder.markdown(answer + "▌")

    placeholder.markdown(answer)
    return answer


def main() -> None:
    st.session_state.setdefault("messages", [])

    with st.sidebar:
        st.markdown(f"### {APP_NAME}")
        healthy = api_healthy()
        st.success("API connected") if healthy else st.error("API offline")

        domains = fetch_domains() if healthy else []
        choice = st.selectbox(
            "Knowledge domain",
            ["All domains", *domains],
            help="Narrow retrieval to one domain, or search everything.",
        )
        scope = None if choice == "All domains" else [choice]

        st.divider()
        if st.button("Clear chat", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        if st.session_state.messages:
            transcript = "\n\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in st.session_state.messages
            )
            st.download_button(
                "Export transcript", transcript, "transcript.txt", "text/plain", use_container_width=True
            )

    if not healthy:
        st.error(f"The API at `{API_BASE}` is not reachable. Start it, then reload.")
        st.code("make api", language="bash")
        return

    if not st.session_state.messages:
        st.markdown(f"## {APP_NAME}")
        st.caption("Ask a question about the indexed documentation.")

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("Ask a question…")
    if not question:
        return

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        try:
            answer = stream_answer(question, scope, placeholder)
        except requests.RequestException as exc:
            answer = f"Request failed: {type(exc).__name__}"
            placeholder.error(answer)
        if scope:
            st.caption(f"{scope[0]} · {datetime.now():%H:%M}")

    st.session_state.messages.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
