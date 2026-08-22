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

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.async_api import Playwright, Route, async_playwright

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

        #: Set while an authorized goto is in flight.  The route handler lets
        #: that navigation (and its redirects) through and gates everything
        #: else.
        self._nav_grant: str | None = None
        #: Called with (url) when a cross-origin navigation is intercepted.
        #: Returns True to allow.  Installed by the server.
        self.on_cross_origin: Any = None
        #: Intercepted-and-aborted targets since the last read, for reporting.
        self.blocked: list[str] = []

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
        await self.page.route("**/*", self._route)

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

    async def _route(self, route: Route) -> None:
        request = route.request
        page = self.page
        if page is None:
            await route.continue_()
            return

        is_top_nav = (
            request.is_navigation_request()
            and request.resource_type == "document"
            and request.frame == page.main_frame
        )
        if not is_top_nav:
            await route.continue_()
            return

        target = request.url
        # An authorized goto, or a redirect within one, proceeds.
        if self._nav_grant is not None and (
            target == self._nav_grant or request.redirected_from is not None
        ):
            await route.continue_()
            return

        current = origin_of(page.url)
        if not current or origin_of(target) == current:
            # Same-origin navigation is ungated in v0 (PREDICTIONS.md).
            await route.continue_()
            return

        allowed = True
        if self.on_cross_origin is not None:
            allowed = bool(self.on_cross_origin(target))
        if allowed:
            await route.continue_()
        else:
            self.blocked.append(target)
            # 204 rather than abort().  Aborting a top-level navigation makes
            # chromium commit an error page, so the agent ends up somewhere
            # neither the operator nor the page asked for; a 204 answer to a
            # navigation is defined to leave the browser where it is, which is
            # what "the page stays put" has to mean.
            await route.fulfill(status=204, body="")

    # -- actions -----------------------------------------------------------

    async def goto(self, url: str) -> None:
        """Navigate to an already-authorized URL."""
        self._nav_grant = url
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
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
