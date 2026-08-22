"""Test harness: two local origins, and a service wired to them.

``evil.test`` is not a placeholder string here.  Chromium's host resolver is
pointed at a second local HTTP server, so a cross-origin escape in T4 is a
real navigation to a real different origin, not a URL that happens to look
foreign.

Model-driven runs (T1-T4) are not implemented yet.  What is implemented is
everything they will need: the fixtures, both origins, and a driver that
calls the six tools exactly as an MCP client would.

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import AsyncIterator, Iterator

from agentlock_browser.config import BrowserConfig
from agentlock_browser.service import BrowserService

FIXTURES = Path(__file__).parent / "fixtures"
RESULTS = Path(__file__).parent / "results"

FIXTURE_HOST = "fixture.test"
EVIL_HOST = "evil.test"
#: A third origin, so a redirect chain can leave the second one and the two
#: hops can be told apart by which server recorded the request.
THIRD_HOST = "third.test"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # noqa: D102 - silence stderr
        pass

    def redirect_to(self, target: str) -> None:
        """Answer with a 302 to ``target``."""
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def serve_page(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _FixtureHandler(_QuietHandler):
    """Serves tests/fixtures, plus a few synthetic redirect routes.

    A 302 cannot come out of a static file, and the redirect cases need one
    served by the allowlisted origin itself.  Every route here is a path no
    fixture file uses.
    """

    REDIRECTS = {
        "/redirect": f"http://{EVIL_HOST}/landed",
        "/chain": f"http://{EVIL_HOST}/a",
        "/same-redirect": f"http://{FIXTURE_HOST}/article.html",
    }

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        target = self.REDIRECTS.get(self.path)
        if target is not None:
            self.redirect_to(target)
            return
        super().do_GET()


class _EvilHandler(_QuietHandler):
    """The second origin.  Records what reached it, which is the whole point:
    a test asserts not just that the gate said DENY but that nothing arrived."""

    hits: list[str] = []

    #: ``/a`` is the middle of the two-hop chain: it redirects on to the third
    #: origin, so a chain that leaves the second origin can be measured.
    REDIRECTS = {"/a": f"http://{THIRD_HOST}/b"}

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        type(self).hits.append(self.path)
        target = self.REDIRECTS.get(self.path)
        if target is not None:
            self.redirect_to(target)
            return
        self.serve_page(b"<!doctype html><title>evil</title><h1>collected</h1>")


class _ThirdHandler(_QuietHandler):
    """The third origin.  Records what reached it, like the second."""

    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        type(self).hits.append(self.path)
        self.serve_page(b"<!doctype html><title>third</title><h1>third</h1>")


@dataclass
class Origins:
    fixture_url: str
    evil_url: str
    chromium_args: list[str]
    third_url: str = ""

    def fixture(self, path: str) -> str:
        return f"{self.fixture_url}/{path.lstrip('/')}"


@contextlib.contextmanager
def serve_origins() -> Iterator[Origins]:
    """Start both origins and yield the chromium args that make them real."""
    _EvilHandler.hits = []
    _ThirdHandler.hits = []
    fixture_server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(_FixtureHandler, directory=str(FIXTURES))
    )
    evil_server = ThreadingHTTPServer(("127.0.0.1", 0), _EvilHandler)
    third_server = ThreadingHTTPServer(("127.0.0.1", 0), _ThirdHandler)
    threads = []
    for server in (fixture_server, evil_server, third_server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        threads.append(thread)

    fixture_port = fixture_server.server_address[1]
    evil_port = evil_server.server_address[1]
    third_port = third_server.server_address[1]
    rules = (
        f"MAP {FIXTURE_HOST} 127.0.0.1:{fixture_port},"
        f"MAP {EVIL_HOST} 127.0.0.1:{evil_port},"
        f"MAP {THIRD_HOST} 127.0.0.1:{third_port}"
    )
    try:
        yield Origins(
            fixture_url=f"http://{FIXTURE_HOST}",
            evil_url=f"http://{EVIL_HOST}",
            third_url=f"http://{THIRD_HOST}",
            chromium_args=[f"--host-resolver-rules={rules}", "--no-proxy-server"],
        )
    finally:
        for server in (fixture_server, evil_server, third_server):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


def evil_hits() -> list[str]:
    """Paths that actually reached the second origin."""
    return list(_EvilHandler.hits)


def third_hits() -> list[str]:
    """Paths that actually reached the third origin."""
    return list(_ThirdHandler.hits)


@contextlib.asynccontextmanager
async def service(
    log_path: Path,
    *,
    operator_text: str = "",
    allowlist: list[str] | None = None,
    chromium_args: list[str] | None = None,
) -> AsyncIterator[BrowserService]:
    """A started BrowserService, configured the way an operator would."""
    config = BrowserConfig(
        allowlist=allowlist or [],
        operator_text=operator_text,
        log_path=str(log_path),
        headless=True,
        chromium_args=list(chromium_args or []),
    )
    svc = BrowserService(config)
    await svc.start()
    try:
        yield svc
    finally:
        await svc.close()
