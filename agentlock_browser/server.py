"""MCP stdio server.

Six tools, each returning ``structuredContent`` against a declared
``outputSchema``.  There is no evaluate tool and no selector argument: the
only way to name an element is an id this server minted, for the page load it
is currently on.

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from agentlock_browser.config import BrowserConfig
from agentlock_browser.models import (
    BackResult,
    ClickResult,
    NavigateResult,
    ReadTextResult,
    SnapshotResult,
    TypeResult,
)
from agentlock_browser.service import BrowserService

__all__ = ["build_server", "ContextElicitor"]


class ContextElicitor:
    """The confirmation channel, as this request's MCP context provides it.

    A thin adapter and nothing more: it does not decide anything, it carries
    the ability to ask a human and the client's own declaration of whether it
    can show them a form.  Constructed per call, because a session's client
    capabilities and its back-channel both belong to the live request.
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def ask(self, message: str, schema: type[BaseModel]):  # noqa: ANN201
        return await self._ctx.elicit(message=message, schema=schema)

    @property
    def client_capabilities(self):  # noqa: ANN201
        return self._ctx.request_context.session.client_capabilities

    @property
    def client_name(self) -> str:
        params = self._ctx.request_context.session.client_params
        info = getattr(params, "client_info", None) if params else None
        return getattr(info, "name", "") or ""

INSTRUCTIONS = """\
Provenance-gated browsing.

Every value this server acts on is gated on where it came from, not on what it
says. A URL or a form value that originated in page content is refused no
matter how the page phrases the request.

- navigate(url=...) is allowed only for a URL from the operator's own message
  or the operator's configured allowlist.
- navigate(link_id=...) follows a link the most recent snapshot returned. Use
  this to follow links on a page; a copied href string will be refused. A link
  leaving both the current origin and the allowlist is put to the operator
  first.
- type(value=...) is allowed only for a value from the operator's own message.
- Clicking is allowed, but a click that would leave the current origin is
  dropped and the page stays where it was. The operator is then asked whether
  to go there; if they say yes the server navigates there itself.

Denials come back as structured results with a reason, not as errors.
"""


def build_server(
    config: BrowserConfig | None = None, service: BrowserService | None = None
) -> MCPServer[BrowserService]:
    """Construct the MCP server.  The browser starts and stops with it."""
    config = config or BrowserConfig.load()
    svc = service or BrowserService(config)

    @asynccontextmanager
    async def lifespan(_: MCPServer[BrowserService]) -> AsyncIterator[BrowserService]:
        await svc.start()
        try:
            yield svc
        finally:
            await svc.close()

    server: MCPServer[BrowserService] = MCPServer(
        name="agentlock-browser",
        title="AgentLock Browser",
        version="0.2.0",
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
    )

    @server.tool(
        name="navigate",
        description=(
            "Navigate the browser. Pass url= for an address from the operator's "
            "message or allowlist, or link_id= for a link id from the most "
            "recent snapshot. Exactly one of the two."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True),
        structured_output=True,
    )
    async def navigate(
        ctx: Context, url: str | None = None, link_id: str | None = None
    ) -> NavigateResult:
        return await svc.navigate(
            url=url, link_id=link_id, elicitor=ContextElicitor(ctx)
        )

    @server.tool(
        name="snapshot",
        description=(
            "Structured inventory of the current page: every element with an "
            "id, role, name, text and href. Ids are valid only for this page "
            "load."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
        structured_output=True,
    )
    async def snapshot() -> SnapshotResult:
        return await svc.snapshot()

    @server.tool(
        name="read_text",
        description=(
            "The page's visible text as separately identified blocks, with the "
            "origin it was read from."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
        structured_output=True,
    )
    async def read_text() -> ReadTextResult:
        return await svc.read_text()

    @server.tool(
        name="click",
        description=(
            "Click the element with this id. A click that would leave the "
            "current origin is refused and the page stays put; the operator "
            "is then asked whether to go there, and the refusal is reported "
            "in 'blocked' either way."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True),
        structured_output=True,
    )
    async def click(ctx: Context, element_id: str) -> ClickResult:
        return await svc.click(element_id, elicitor=ContextElicitor(ctx))

    @server.tool(
        name="type",
        description=(
            "Type a value into the element with this id. Only values from the "
            "operator's own message are permitted."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True),
        structured_output=True,
    )
    async def type_(ctx: Context, element_id: str, value: str) -> TypeResult:
        return await svc.type(element_id, value, elicitor=ContextElicitor(ctx))

    @server.tool(
        name="back",
        description="Go back one entry in this tab's history.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True),
        structured_output=True,
    )
    async def back() -> BackResult:
        return await svc.back()

    return server
