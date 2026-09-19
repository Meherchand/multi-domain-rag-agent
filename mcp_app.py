#!/usr/bin/env python3
"""Entry point for the MCP server.

python mcp_app.py                          # stdio (editors, desktop clients)
python mcp_app.py --transport sse --port 8100   # HTTP/SSE (remote clients)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.mcp_server.server import mcp  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-Domain RAG Agent — MCP server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8100, help="Port for the sse transport")
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.settings.port = args.port
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
