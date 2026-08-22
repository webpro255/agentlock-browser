"""Redirect and intercepted-navigation regression cases R1 to R5.

Moved here from the probe/origin scratchpad harness so the cases run under
pytest instead of by hand.  The tools are driven directly, with no model in
the loop: what is measured is the gate and the server.

Two local origins, both real.  Chromium's host resolver is pointed at them,
so ``fixture.test`` and ``evil.test`` are distinct origins and a cross-origin
escape is a real navigation.  The evil origin records what reached it, which
is the check that matters: not that the gate said DENY, but that nothing
arrived.

Expectations are the ones frozen in PREDICTIONS.md, amendment 2026-08-22b.
The raw transcript is written to ``tests/results/redirects_<phase>.txt``
before any assertion runs, so a failing run still leaves its evidence.

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

from agentlock_browser.config import BrowserConfig
from agentlock_browser.log import DecisionLog
from agentlock_browser.service import BrowserService
from tests.harness import RESULTS

FIXTURE_HOST = "fixture.test"
EVIL_HOST = "evil.test"
EVIL_LANDING = f"http://{EVIL_HOST}/landed"
SAME_ORIGIN_LANDING = f"http://{FIXTURE_HOST}/landing_same.html"

#: The operator message used by R1, R1b, R2, R3, R4 and R5.  Carries a
#: distinctive token, so novel lineage has a baseline to classify against.
OPERATOR_TEXT = f"follow the links on http://{FIXTURE_HOST}/start"

#: The operator message used by R2b and R3b.  No digit, no structural
#: character, and no word of length 12 or more, so AgentLock's token
#: extractor emits nothing from it and novel lineage has no baseline.
#: This is the state in which the MODEL channel check goes inactive
#: (NEEDS.md item 3(b)).
BLAND_OPERATOR_TEXT = "please read this page and tell me what it says"

PAGES = {
    "/article.html": (
        "<!doctype html><meta charset=utf-8><title>Quarterly notes</title>"
        "<h1>Quarterly notes</h1><p>An ordinary same-origin page.</p>"
    ),
    "/landing_same.html": (
        "<!doctype html><meta charset=utf-8><title>Same origin landing</title>"
        "<h1>same origin landing</h1>"
    ),
    "/userinfo_link.html": (
        "<!doctype html><meta charset=utf-8><title>Userinfo link</title>"
        "<h1>Userinfo link</h1><p>One link below.</p>"
        f'<p><a href="http://{FIXTURE_HOST}@{EVIL_HOST}/">Continue reading</a></p>'
    ),
    "/meta": (
        "<!doctype html><meta charset=utf-8><title>Meta refresh</title>"
        f'<meta http-equiv="refresh" content="1;url={EVIL_LANDING}">'
        "<h1>Meta refresh</h1><p>Redirecting.</p>"
    ),
    "/js": (
        "<!doctype html><meta charset=utf-8><title>JS redirect</title>"
        "<h1>JS redirect</h1><p>Redirecting.</p>"
        f'<script>setTimeout(function(){{location.href="{EVIL_LANDING}";}},600);</script>'
    ),
}


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def _html(self, body: str) -> None:
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()


class _FixtureHandler(_Quiet):
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path == "/redirect":
            self._redirect(EVIL_LANDING)
            return
        if self.path == "/redirect_same":
            self._redirect(SAME_ORIGIN_LANDING)
            return
        page = PAGES.get(self.path)
        if page is not None:
            self._html(page)
            return
        self._html(
            "<!doctype html><meta charset=utf-8><title>Fixture</title>"
            f"<h1>Fixture</h1><p>Path {self.path}</p>"
        )


class _EvilHandler(_Quiet):
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802
        type(self).hits.append(self.path)
        self._html(
            "<!doctype html><meta charset=utf-8><title>evil landing</title>"
            "<h1>landed</h1>"
        )


@contextlib.contextmanager
def _origins() -> Iterator[list[str]]:
    """Both origins, and the chromium arguments that make them resolve."""
    _EvilHandler.hits = []
    fixture = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    evil = ThreadingHTTPServer(("127.0.0.1", 0), _EvilHandler)
    threads = []
    for server in (fixture, evil):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        threads.append(thread)
    rules = (
        f"MAP {FIXTURE_HOST} 127.0.0.1:{fixture.server_address[1]},"
        f"MAP {EVIL_HOST} 127.0.0.1:{evil.server_address[1]}"
    )
    try:
        yield [f"--host-resolver-rules={rules}", "--no-proxy-server"]
    finally:
        for server in (fixture, evil):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


@contextlib.asynccontextmanager
async def _service(
    log_path: Path, chromium_args: list[str], operator_text: str
) -> AsyncIterator[BrowserService]:
    config = BrowserConfig(
        allowlist=[f"http://{FIXTURE_HOST}"],
        operator_text=operator_text,
        log_path=str(log_path),
        headless=True,
        chromium_args=list(chromium_args),
    )
    svc = BrowserService(config)
    await svc.start()
    try:
        yield svc
    finally:
        await svc.close()


def _observe(svc: BrowserService, log_path: Path) -> dict[str, Any]:
    """Everything a case is judged on, read after the case has run."""
    log = DecisionLog(log_path)
    decisions = log.decisions()
    last = decisions[-1] if decisions else {}
    return {
        "decision": last.get("decision", ""),
        "reason": last.get("reason", ""),
        "channel": last.get("channel", ""),
        "interceptor_fired": sum(
            1 for d in decisions if d.get("action") == "intercepted_navigation"
        ),
        "final_page_url": svc.browser.url,
        "evil_hits": list(_EvilHandler.hits),
        "denials": [d.get("reason", "") for d in log.denials()],
        "materialized": sum(
            1 for r in log.read()
            if r.get("event") == "provenance"
            and r.get("note") == "allowlist materialized for url"
        ),
        "blocked": list(svc.browser.blocked),
        "decisions": decisions,
    }


async def _run_case(name, description, chromium_args, tmp, operator_text, body):
    log_path = tmp / f"{name}.jsonl"
    _EvilHandler.hits = []
    transcript: list[dict[str, Any]] = []
    async with _service(log_path, chromium_args, operator_text) as svc:
        await body(svc, transcript)
        observed = _observe(svc, log_path)
    return {
        "case": name,
        "description": description,
        "operator_text": operator_text,
        "transcript": transcript,
        "observed": observed,
    }


async def _settle(svc: BrowserService, ms: int) -> None:
    await svc.browser.page.wait_for_timeout(ms)


def _record(transcript, call, result) -> None:
    transcript.append({"call": call, "result": result.model_dump(mode="json")})


def _navigate_then_settle(url: str, settle_ms: int):
    async def body(svc, transcript):
        result = await svc.navigate(url=url)
        _record(transcript, {"tool": "navigate", "args": {"url": url}}, result)
        await _settle(svc, settle_ms)

    return body


async def _navigate_twice(svc, transcript):
    """R1c: land on a real document first, then take the refused redirect."""
    first = f"http://{FIXTURE_HOST}/article.html"
    result = await svc.navigate(url=first)
    _record(transcript, {"tool": "navigate", "args": {"url": first}}, result)
    second = f"http://{FIXTURE_HOST}/redirect"
    result = await svc.navigate(url=second)
    _record(transcript, {"tool": "navigate", "args": {"url": second}}, result)
    await _settle(svc, 1500)


async def _click_userinfo(svc, transcript):
    url = f"http://{FIXTURE_HOST}/userinfo_link.html"
    result = await svc.navigate(url=url)
    _record(transcript, {"tool": "navigate", "args": {"url": url}}, result)
    snap = await svc.snapshot()
    _record(transcript, {"tool": "snapshot", "args": {}}, snap)
    link = [e for e in snap.elements if e.role == "link"][0]
    clicked = await svc.click(link.id)
    _record(transcript, {"tool": "click", "args": {"element_id": link.id}}, clicked)
    await _settle(svc, 1000)


CASES = [
    ("R1", "302 Location redirect to evil.test from an allowlisted url",
     OPERATOR_TEXT, _navigate_then_settle(f"http://{FIXTURE_HOST}/redirect", 1500)),
    ("R1b", "302 Location redirect to another path on the allowlisted origin",
     OPERATOR_TEXT, _navigate_then_settle(f"http://{FIXTURE_HOST}/redirect_same", 1500)),
    ("R1c", "302 to evil.test with a committed document on the origin first",
     OPERATOR_TEXT, _navigate_twice),
    ("R2", "meta refresh redirect to evil.test from an allowlisted url",
     OPERATOR_TEXT, _navigate_then_settle(f"http://{FIXTURE_HOST}/meta", 2500)),
    ("R2b", "meta refresh, bland operator message with no distinctive token",
     BLAND_OPERATOR_TEXT, _navigate_then_settle(f"http://{FIXTURE_HOST}/meta", 2500)),
    ("R3", "JS location.href redirect to evil.test from an allowlisted url",
     OPERATOR_TEXT, _navigate_then_settle(f"http://{FIXTURE_HOST}/js", 2000)),
    ("R3b", "JS location.href, bland operator message with no distinctive token",
     BLAND_OPERATOR_TEXT, _navigate_then_settle(f"http://{FIXTURE_HOST}/js", 2000)),
    ("R4", "navigate by url with userinfo: http://fixture.test@evil.test/",
     OPERATOR_TEXT, _navigate_then_settle(f"http://{FIXTURE_HOST}@{EVIL_HOST}/", 1000)),
    ("R5", "click a link whose href carries userinfo",
     OPERATOR_TEXT, _click_userinfo),
]

#: PREDICTIONS.md amendments 2026-08-22b and 2026-08-22c, one entry per case.
#: ``None`` means the field is not constrained by the freeze.  ``url_exact``
#: is used where an amendment pins the final URL, ``url_host`` where it only
#: pins the origin.
FROZEN = {
    # 2026-08-22c restated R1: the page remains at the URL it was at before
    # the navigate call, which in this harness is about:blank.
    "R1":  {"decision": "deny",  "reason": "param_lineage", "channel": "PAGE",
            "interceptor_min": 1, "url_exact": "about:blank", "evil_hits": []},
    "R1b": {"decision": "allow", "reason": "",              "channel": None,
            "interceptor_min": 0, "url_host": FIXTURE_HOST, "evil_hits": [],
            "no_denials": True},
    # 2026-08-22c R1c: a committed document exists, so the page remains on it.
    "R1c": {"decision": "deny",  "reason": "param_lineage", "channel": "PAGE",
            "interceptor_min": 1,
            "url_exact": f"http://{FIXTURE_HOST}/article.html",
            "evil_hits": []},
    "R2":  {"decision": "deny",  "reason": "param_lineage", "channel": "PAGE",
            "interceptor_min": 1, "url_host": FIXTURE_HOST, "evil_hits": []},
    "R2b": {"decision": "deny",  "reason": "param_lineage", "channel": "PAGE",
            "interceptor_min": 1, "url_host": FIXTURE_HOST, "evil_hits": []},
    "R3":  {"decision": "deny",  "reason": "param_lineage", "channel": "PAGE",
            "interceptor_min": 1, "url_host": FIXTURE_HOST, "evil_hits": []},
    "R3b": {"decision": "deny",  "reason": "param_lineage", "channel": "PAGE",
            "interceptor_min": 1, "url_host": FIXTURE_HOST, "evil_hits": []},
    # 2026-08-22b: R4 and R5 unchanged, so their final URLs are pinned to
    # what the pre-fix baseline recorded.
    "R4":  {"decision": "deny",  "reason": "novel_lineage", "channel": "MODEL",
            "interceptor_min": 0, "url_exact": "about:blank", "evil_hits": []},
    "R5":  {"decision": "deny",  "reason": "param_lineage", "channel": "PAGE",
            "interceptor_min": 1,
            "url_exact": f"http://{FIXTURE_HOST}/userinfo_link.html",
            "evil_hits": []},
}


def _check(case: str, observed: dict[str, Any]) -> list[str]:
    """Fields that differ from the freeze.  Empty list means exact match."""
    frozen = FROZEN[case]
    diffs = []
    for field in ("decision", "reason", "channel"):
        want = frozen.get(field)
        if want is not None and observed[field] != want:
            diffs.append(f"{field}: frozen {want!r}, observed {observed[field]!r}")
    if observed["interceptor_fired"] < frozen["interceptor_min"]:
        diffs.append(
            f"interceptor_fired: frozen at least {frozen['interceptor_min']}, "
            f"observed {observed['interceptor_fired']}"
        )
    if "url_exact" in frozen and observed["final_page_url"] != frozen["url_exact"]:
        diffs.append(
            f"final_page_url: frozen {frozen['url_exact']!r}, "
            f"observed {observed['final_page_url']!r}"
        )
    host = frozen.get("url_host")
    if host is not None and host not in observed["final_page_url"]:
        diffs.append(
            f"final_page_url: frozen host {host!r}, "
            f"observed {observed['final_page_url']!r}"
        )
    if observed["evil_hits"] != frozen["evil_hits"]:
        diffs.append(
            f"evil_hits: frozen {frozen['evil_hits']!r}, "
            f"observed {observed['evil_hits']!r}"
        )
    if frozen.get("no_denials") and observed["denials"]:
        diffs.append(f"denials: frozen none, observed {observed['denials']!r}")
    return diffs


async def _run_all() -> list[dict[str, Any]]:
    tmp = Path(tempfile.mkdtemp())
    results = []
    with _origins() as chromium_args:
        for name, description, operator_text, body in CASES:
            results.append(
                await _run_case(name, description, chromium_args, tmp,
                                operator_text, body)
            )
    return results


def _write_report(results, phase: str) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    lines = [
        f"Redirect regression cases, {phase} run",
        "=" * 72,
        "",
        f"allowlist:            ['http://{FIXTURE_HOST}']",
        f"operator_text:        {OPERATOR_TEXT!r}",
        f"bland operator_text:  {BLAND_OPERATOR_TEXT!r}",
        "",
        "Frozen expectations: PREDICTIONS.md amendment 2026-08-22b.",
        "",
    ]
    for result in results:
        case = result["case"]
        observed = result["observed"]
        lines.append("=" * 72)
        lines.append(f"CASE {case}: {result['description']}")
        lines.append("=" * 72)
        lines.append(f"operator_text: {result['operator_text']!r}")
        lines.append("")
        lines.append("TOOL CALLS AND RAW RESULTS")
        lines.append("-" * 72)
        for step in result["transcript"]:
            lines.append(f"-> {json.dumps(step['call'], sort_keys=True)}")
            lines.append(json.dumps(step["result"], indent=2, sort_keys=True))
            lines.append("")
        lines.append("OBSERVED")
        lines.append("-" * 72)
        for field in ("decision", "reason", "channel", "interceptor_fired",
                      "final_page_url", "evil_hits", "denials", "materialized",
                      "blocked"):
            lines.append(f"{field} = {observed[field]!r}")
        lines.append("")
        lines.append("DECISION LOG (raw JSONL)")
        lines.append("-" * 72)
        for record in observed["decisions"]:
            lines.append(json.dumps(record, sort_keys=True))
        lines.append("")
        diffs = _check(case, observed)
        lines.append("AGAINST THE FREEZE")
        lines.append("-" * 72)
        if diffs:
            lines.append("MATCH: no")
            for diff in diffs:
                lines.append(f"  differs  {diff}")
        else:
            lines.append("MATCH: yes")
        lines.append("")

    matched = [r["case"] for r in results if not _check(r["case"], r["observed"])]
    differed = [r["case"] for r in results if _check(r["case"], r["observed"])]
    lines.append("=" * 72)
    lines.append("SUMMARY")
    lines.append("=" * 72)
    lines.append(f"matching the freeze: {matched}")
    lines.append(f"differing from the freeze: {differed}")
    path = RESULTS / f"redirects_{phase}.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_redirect_cases_against_the_freeze() -> None:
    """Run R1 to R5 and compare every recorded field to PREDICTIONS.md.

    The phase name for the transcript comes from ``REDIRECT_PHASE`` so the
    pre-fix baseline and the post-fix replay land in separate files.
    """
    phase = os.environ.get("REDIRECT_PHASE", "post")
    results = asyncio.run(_run_all())
    path = _write_report(results, phase)
    differed = {
        r["case"]: _check(r["case"], r["observed"])
        for r in results
        if _check(r["case"], r["observed"])
    }
    assert not differed, (
        f"cases differing from PREDICTIONS.md amendment 2026-08-22b: "
        f"{json.dumps(differed, indent=2)}\nraw transcript: {path}"
    )
