"""The browser this server owns.

agentlock-browser drives chromium through Playwright directly.  It does not
wrap @playwright/mcp and shares no code with it.  The probe in probe/REPORT.md
is the reason: that server returns one undifferentiated text blob per call,
identifies elements with opaque server-side refs, accepts raw CSS selectors in
the same field as those refs, and exposes arbitrary JavaScript through
``browser_evaluate``.  None of that can carry provenance.

What this module guarantees instead:

* every result is structured, and page text arrives as separately identified
  elements or blocks -- never as one string;
* element ids are stable for the current page load and regenerate on
  navigation, so an id cannot outlive the page it describes;
* there is no evaluate tool.  The extraction script below is a fixed,
  server-authored constant; no model input reaches it, and nothing in the MCP
  surface can run JavaScript.

Navigation is intercepted through the CDP ``Fetch`` domain rather than
Playwright's ``page.route``.  That is not a preference: ``page.route`` never
offers a redirect hop to a handler, so a 302 to another origin was followed
before anything could gate it (probe/origin/REPORT.md R1,
probe/cdp/REPORT.md).  ``Fetch.requestPaused`` does surface the hop, and
carries ``redirectedRequestId`` pointing back at the request it came from.
This makes the server chromium-only.

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Playwright, async_playwright

from agentlock_browser.config import BrowserConfig, origin_of

__all__ = ["BrowserSession", "PageElement", "TextBlock"]

#: Elements an inventory should name: everything interactive, plus the
#: headings and text containers that make a page readable.
SNAPSHOT_SEL = (
    "a[href], button, input, select, textarea, "
    "h1, h2, h3, h4, h5, h6, p, li, label"
)

#: Block-level containers for read_text.  Generic containers are included so
#: text in a bare <div> is not silently missed; the extractor keeps only leaf
#: matches, so a paragraph's text is attributed to the paragraph and not also
#: to every ancestor that happens to match.
BLOCK_SEL = (
    "h1, h2, h3, h4, h5, h6, p, li, blockquote, pre, td, th, dt, dd, "
    "figcaption, div, section, article, main, aside, span"
)

#: Fixed extraction script.  A constant, never interpolated with model input.
_EXTRACT_JS = """
({ selector, leafOnly }) => {
  const nodes = Array.from(document.querySelectorAll(selector));
  const visible = (el) => {
    const s = window.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0')
      return false;
    const r = el.getBoundingClientRect();
    return (r.width > 0 && r.height > 0) || el.tagName === 'OPTION';
  };
  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'submit' || t === 'button') return 'button';
      return 'textbox';
    }
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'li') return 'listitem';
    if (tag === 'p') return 'paragraph';
    if (tag === 'label') return 'label';
    return tag;
  };
  const nameOf = (el) => (
    el.getAttribute('aria-label') ||
    el.getAttribute('alt') ||
    el.getAttribute('placeholder') ||
    el.getAttribute('title') ||
    (el.tagName === 'INPUT' ? (el.getAttribute('name') || '') : '') ||
    (el.innerText || '').trim().slice(0, 120)
  );
  return nodes.map((el, i) => ({
    index: i,
    leaf: el.querySelector(selector) === null,
    role: roleOf(el),
    name: (nameOf(el) || '').trim().slice(0, 200),
    text: (el.innerText || '').trim().slice(0, 2000),
    href: el.tagName === 'A' ? (el.href || '') : '',
    visible: visible(el),
  })).filter((item) => !leafOnly || item.leaf);
}
"""


#: Only main-frame document requests are paused.  Subresources are not
#: navigation and are never gated, so pausing them would cost latency for
#: nothing.
_FETCH_PATTERNS = [
    {"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}
]

#: A denied navigation is answered with 204, not failed.  Measured in
#: probe/cdp/REPORT.md: ``Fetch.failRequest`` leaves the page on
#: ``chrome-error://chromewebdata/`` even when a document was committed
#: before, while a 204 leaves it exactly where it was.  "The page stays put"
#: has to mean the URL it was at before the call.
_DENY_RESPONSE_CODE = 204


@dataclass
class PageElement:
    id: str
    role: str
    name: str
    text: str
    href: str


@dataclass
class TextBlock:
    id: str
    text: str


class BrowserSession:
    """Owns one chromium page and the identifiers that describe it."""

    def __init__(self, config: BrowserConfig) -> None:
        self.config = config
        self._pw: Playwright | None = None
        self._browser: Any = None
        self._context: Any = None
        self.page: Any = None

        #: Increments on every committed main-frame navigation.  Element ids
        #: carry it, so an id minted before a navigation can never resolve
        #: after one.
        self.epoch = 0
        #: Ids returned by the most recent snapshot, and the hrefs they
        #: resolved to.  navigate(link_id) is checked against exactly this.
        self.snapshot_ids: dict[str, str] = {}
        self.snapshot_epoch = -1

        #: Set while an authorized goto is in flight.  Its origin is the
        #: origin the navigation was authorized for, which is what a redirect
        #: hop is compared against.
        self._nav_grant: str | None = None
        #: Called with (url, origin, redirected_from) when a cross-origin
        #: navigation is intercepted.  Returns True to allow.  Installed by
        #: the server.
        self.on_cross_origin: Any = None
        #: Intercepted-and-refused targets since the last read, for reporting.
        self.blocked: list[str] = []
        #: Anything the interceptor itself failed on.  Recorded rather than
        #: swallowed: the handler fails closed, so a bug here stops browsing
        #: rather than quietly letting a navigation through.
        self.interceptor_errors: list[str] = []
        #: Error text from the last goto, when the navigation was refused.
        self.last_navigation_error: str = ""

        self._cdp: Any = None
        self._main_frame_id: str = ""
        self._loop: Any = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.config.headless,
            args=list(self.config.chromium_args),
        )
        self._context = await self._browser.new_context(
            user_agent=self.config.user_agent,
        )
        self._context.set_default_navigation_timeout(self.config.nav_timeout_ms)
        self._context.set_default_timeout(self.config.action_timeout_ms)
        self.page = await self._context.new_page()
        self.page.on("framenavigated", self._on_frame_navigated)

        self._loop = asyncio.get_running_loop()
        self._cdp = await self._context.new_cdp_session(self.page)
        frame_tree = await self._cdp.send("Page.getFrameTree")
        self._main_frame_id = frame_tree["frameTree"]["frame"]["id"]
        await self._cdp.send("Fetch.enable", {"patterns": _FETCH_PATTERNS})
        self._cdp.on("Fetch.requestPaused", self._on_request_paused)

    async def close(self) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                await closer.close()
        if self._pw is not None:
            await self._pw.stop()
        self._pw = self._browser = self._context = self.page = None

    async def __aenter__(self) -> BrowserSession:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # -- navigation interception ------------------------------------------

    def _on_frame_navigated(self, frame: Any) -> None:
        if self.page is not None and frame == self.page.main_frame:
            self.epoch += 1
            self.snapshot_ids = {}

    def _on_request_paused(self, event: dict[str, Any]) -> None:
        """CDP hands this to us synchronously; the decision runs as a task.

        The request stays paused until the task answers it, so nothing races
        ahead of the gate.
        """
        if self._loop is not None:
            self._loop.create_task(self._handle_paused(event))

    def _authorized_origin(self) -> str:
        """The origin this navigation is allowed to be on.

        While an authorized goto is in flight that is the origin the gate
        approved, not the page's current origin: during a redirect chain the
        page has not moved yet, and comparing against where it still happens
        to be would let the first hop off the authorized origin through.
        """
        if self._nav_grant is not None:
            return origin_of(self._nav_grant)
        return origin_of(self.page.url if self.page else "")

    async def _handle_paused(self, event: dict[str, Any]) -> None:
        request_id = event.get("requestId", "")
        target = event.get("request", {}).get("url", "")
        redirected_from = event.get("redirectedRequestId")
        try:
            if event.get("frameId") != self._main_frame_id:
                # A subframe document.  Not navigation of the page the tools
                # act on, and not gated in v0.
                await self._continue(request_id)
                return

            if self._nav_grant is not None and target == self._nav_grant:
                # The navigation navigate() just authorized.
                await self._continue(request_id)
                return

            authorized = self._authorized_origin()
            if not authorized or origin_of(target) == authorized:
                # Same-origin navigation is ungated in v0 (PREDICTIONS.md).
                await self._continue(request_id)
                return

            if await self._authorized_cross_origin(target, authorized,
                                                   redirected_from):
                await self._continue(request_id)
            else:
                self.blocked.append(target)
                await self._refuse(request_id)
        except Exception as exc:  # noqa: BLE001 - fails closed, and says so
            self.interceptor_errors.append(
                f"{type(exc).__name__}: {exc} (request {request_id} for {target})"
            )
            try:
                await self._refuse(request_id)
            except Exception as inner:  # noqa: BLE001
                self.interceptor_errors.append(
                    f"refuse failed: {type(inner).__name__}: {inner}"
                )

    async def _continue(self, request_id: str) -> None:
        await self._cdp.send("Fetch.continueRequest", {"requestId": request_id})

    async def _refuse(self, request_id: str) -> None:
        """Answer a refused navigation with 204, leaving the page where it is."""
        await self._cdp.send("Fetch.fulfillRequest", {
            "requestId": request_id,
            "responseCode": _DENY_RESPONSE_CODE,
            "responseHeaders": [],
            "body": "",
        })

    async def _authorized_cross_origin(
        self, target: str, origin: str, redirected_from: str | None
    ) -> bool:
        """Ask the owner of this session whether the navigation may proceed."""
        if self.on_cross_origin is None:
            return True
        return bool(self.on_cross_origin(target, origin, redirected_from))

    # -- actions -----------------------------------------------------------

    async def goto(self, url: str) -> None:
        """Navigate to an already-authorized URL.

        A navigation the interceptor refuses mid-flight surfaces here as an
        aborted goto.  That is a decision this server made, not a failure, so
        it is recorded rather than raised: the page is still on the URL it was
        on before the call, and the caller reports that.
        """
        self.last_navigation_error = ""
        self._nav_grant = url
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            self.last_navigation_error = f"{type(exc).__name__}: {exc}".splitlines()[0]
        finally:
            self._nav_grant = None

    async def back(self) -> bool:
        response = await self.page.go_back(wait_until="domcontentloaded")
        return response is not None

    @property
    def url(self) -> str:
        return self.page.url if self.page else ""

    @property
    def origin(self) -> str:
        return origin_of(self.url)

    async def title(self) -> str:
        return await self.page.title()

    async def _extract(
        self, selector: str, leaf_only: bool = False
    ) -> list[dict[str, Any]]:
        return await self.page.evaluate(
            _EXTRACT_JS, {"selector": selector, "leafOnly": leaf_only}
        )

    async def snapshot(self) -> list[PageElement]:
        """Structured element inventory for the current page load.

        Ids are ``<epoch>-e<index>``: deterministic from document order, so
        two snapshots of the same unchanged page return the same ids, and the
        epoch makes every id from a previous page load unresolvable.
        """
        raw = await self._extract(SNAPSHOT_SEL)
        elements = [
            PageElement(
                id=f"{self.epoch}-e{item['index']}",
                role=item["role"],
                name=item["name"],
                text=item["text"],
                href=item["href"],
            )
            for item in raw
            if item["visible"] or item["href"]
        ]
        self.snapshot_ids = {e.id: e.href for e in elements}
        self.snapshot_epoch = self.epoch
        return elements

    async def read_text(self) -> list[TextBlock]:
        """Page text as separately identified blocks.  Never one string."""
        raw = await self._extract(BLOCK_SEL, leaf_only=True)
        return [
            TextBlock(id=f"{self.epoch}-b{item['index']}", text=item["text"])
            for item in raw
            if item["visible"] and item["text"]
        ]

    def resolve_link(self, element_id: str) -> str | None:
        """The href a link id resolved to in the most recent snapshot.

        Returns None when the id is unknown, or when it belongs to a previous
        page load.  This is the whole freshness rule.
        """
        if self.snapshot_epoch != self.epoch:
            return None
        href = self.snapshot_ids.get(element_id)
        return href or None

    def _locator(self, element_id: str) -> Any:
        """Resolve an element id back to a live locator.

        The id encodes the page-load epoch and the element's index in document
        order under a fixed selector.  A mismatched epoch is refused rather
        than silently resolved against a different page.
        """
        try:
            epoch_str, kind_index = element_id.split("-", 1)
            epoch = int(epoch_str)
            kind, index = kind_index[0], int(kind_index[1:])
        except (ValueError, IndexError):
            raise KeyError(f"malformed element id: {element_id!r}") from None
        if kind != "e":
            # read_text block ids ("<epoch>-b<n>") index a different node list.
            # Resolving one here would act on a different element than the id
            # names, so it is refused rather than coerced.
            raise KeyError(
                f"{element_id!r} is a text-block id; click and type take "
                f"element ids from snapshot (\"<epoch>-e<n>\")"
            )
        if epoch != self.epoch:
            raise KeyError(
                f"element id {element_id!r} belongs to page load {epoch}, "
                f"current page load is {self.epoch}"
            )
        return self.page.locator(SNAPSHOT_SEL).nth(index)

    async def click(self, element_id: str) -> None:
        """Click, then let any navigation the click triggered be decided.

        Without the settle wait the click returns before the route handler has
        seen the navigation request, and the caller reads an interception
        result that has not happened yet.
        """
        await self._locator(element_id).click()
        await self.page.wait_for_timeout(self.config.settle_ms)

    async def fill(self, element_id: str, value: str) -> None:
        await self._locator(element_id).fill(value)
