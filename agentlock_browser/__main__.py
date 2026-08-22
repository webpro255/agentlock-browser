"""Entry point: ``agentlock-browser`` runs the MCP server on stdio.

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import anyio

from agentlock_browser.config import BrowserConfig
from agentlock_browser.server import build_server


def main() -> None:
    server = build_server(BrowserConfig.load())
    anyio.run(server.run_stdio_async)


if __name__ == "__main__":
    main()
