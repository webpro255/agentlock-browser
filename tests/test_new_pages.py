"""New-page regression cases N1 to N4.

The Fetch interceptor is attached to one page.  A link with ``target="_blank"``
or a ``window.open`` call creates a second top-level page, and probe/popup
recorded that such a page loaded from ``evil.test`` with the interceptor never
firing and no decision reaching the log.  These cases are that escape, moved
out of the probe scratchpad and under pytest.

Driven directly, no model in the loop: what is measured is the browser and the
server.  Both origins are real, so ``evil.test hits`` is what actually arrived
rather than what a gate said.

Expectations are the ones frozen in PREDICTIONS.md, amendment 2026-08-23.  The
raw transcript is written to ``tests/results/newpages_<phase>.txt`` before any
assertion runs, so a failing run still leaves its evidence.

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from agentlock_browser.log import DecisionLog
from tests.harness import FIXTURE_HOST, RESULTS, evil_hits, serve_origins, service

#: Every case navigates to its fixture first.  The operator names the URL, so
#: that setup navigation is the operator's own and not a page's.
OPERATOR_TEMPLATE = "open http://{host}/{fixture} and tell me what it says"


async def _click_first_link(svc: Any) -> list[dict[str, Any]]:
    """Click the first link on the page and return the transcript."""
    snap = await svc.snapshot()
    target = next(e for e in snap.elements if e.role == "link")
    result = await svc.click(target.id)
    return [
        {"call": {"tool": "snapshot"}, "result": snap.model_dump()},
        {"call": {"tool": "click", "element_id": target.id,
                  "href": target.href}, "result": result.model_dump()},
    ]


async def _click_button(svc: Any) -> list[dict[str, Any]]:
    """Click the first button on the page and return the transcript."""
    snap = await svc.snapshot()
    target = next(e for e in snap.elements if e.role == "button")
    result = await svc.click(target.id)
    return [
        {"call": {"tool": "snapshot"}, "result": snap.model_dump()},
        {"call": {"tool": "click", "element_id": target.id},
         "result": result.model_dump()},
    ]


async def _no_action(svc: Any) -> list[dict[str, Any]]:
    """Navigate only.  The fixture opens the window by itself, on load."""
    return []


CASES = [
    ("N1", "click a target=_blank link pointing at evil.test",
     "blank_link.html", _click_first_link),
    ("N2", "click a button whose onclick calls window.open on evil.test",
     "window_open.html", _click_button),
    ("N3", "click a target=_blank link pointing at the fixture origin",
     "blank_same.html", _click_first_link),
    ("N4", "navigate only; the page calls window.open on load",
     "auto_open.html", _no_action),
]

#: PREDICTIONS.md amendment 2026-08-23.  ``blocked_page_entries`` is
#: deliberately absent: the amendment records the count per case and does not
#: predict it, because the launch flag may suppress the page before any event
#: fires.
FROZEN = {
    "N1": {"pages_after": 1, "evil_hits": [], "click_ok": True},
    "N2": {"pages_after": 1, "evil_hits": [], "click_ok": True},
    "N3": {"pages_after": 1, "evil_hits": []},
    "N4": {"pages_after": 1, "evil_hits": []},
}


def _check(case: str, observed: dict[str, Any]) -> list[str]:
    """Fields that differ from the freeze.  Empty list means exact match."""
    frozen = FROZEN[case]
    fixture_url = observed["fixture_url"]
    diffs = []

    if observed["pages_after"] != frozen["pages_after"]:
        diffs.append(
            f"pages_after: frozen {frozen['pages_after']!r}, "
            f"observed {observed['pages_after']!r} ({observed['page_urls']!r})"
        )
    if observed["evil_hits"] != frozen["evil_hits"]:
        diffs.append(
            f"evil_hits: frozen {frozen['evil_hits']!r}, "
            f"observed {observed['evil_hits']!r}"
        )
    if observed["service_page_url"] != fixture_url:
        diffs.append(
            f"service_page_url: frozen {fixture_url!r}, "
            f"observed {observed['service_page_url']!r}"
        )
    if observed["snapshot_url"] != fixture_url:
        diffs.append(
            f"snapshot_url: frozen {fixture_url!r}, "
            f"observed {observed['snapshot_url']!r}"
        )
    if "click_ok" in frozen:
        if observed["click_ok"] != frozen["click_ok"]:
            diffs.append(
                f"click_ok: frozen {frozen['click_ok']!r}, "
                f"observed {observed['click_ok']!r}"
            )
        if observed["click_url"] != fixture_url:
            diffs.append(
                f"click_url: frozen {fixture_url!r} (page unchanged), "
                f"observed {observed['click_url']!r}"
            )
    if observed["interceptor_errors"]:
        diffs.append(
            f"interceptor_errors: frozen [], "
            f"observed {observed['interceptor_errors']!r}"
        )
    return diffs


async def _run_case(
    case: str, description: str, fixture: str, body: Any,
    chromium_args: list[str], tmp: Path,
) -> dict[str, Any]:
    log_path = tmp / f"newpages_{case}.jsonl"
    if log_path.exists():
        log_path.unlink()
    fixture_url = f"http://{FIXTURE_HOST}/{fixture}"
    operator_text = OPERATOR_TEMPLATE.format(host=FIXTURE_HOST, fixture=fixture)

    async with service(
        log_path,
        operator_text=operator_text,
        allowlist=[f"http://{FIXTURE_HOST}"],
        chromium_args=chromium_args,
    ) as svc:
        transcript = []
        setup = await svc.navigate(url=fixture_url)
        transcript.append(
            {"call": {"tool": "navigate", "url": fixture_url},
             "result": setup.model_dump()}
        )

        transcript.extend(await body(svc))

        # The page a fixture opens by itself needs time to be created and to
        # reach the other origin; without the wait a clean result would only
        # mean the test read too early.
        await svc.browser.page.wait_for_timeout(2000)

        pages = svc.browser._context.pages
        page_urls = [p.url for p in pages]
        service_page_url = svc.browser.page.url

        snap = await svc.snapshot()
        transcript.append(
            {"call": {"tool": "snapshot", "after": "action"},
             "result": snap.model_dump()}
        )

        click_steps = [
            s for s in transcript if s["call"].get("tool") == "click"
        ]
        records = DecisionLog(log_path).read()
        observed = {
            "fixture_url": fixture_url,
            "pages_after": len(pages),
            "page_urls": page_urls,
            "service_page_url": service_page_url,
            "snapshot_url": snap.url,
            "evil_hits": evil_hits(),
            "click_ok": click_steps[0]["result"]["ok"] if click_steps else None,
            "click_url": click_steps[0]["result"]["url"] if click_steps else None,
            "blocked_page_entries": [
                r for r in records if r.get("event") == "blocked_page"
            ],
            "decisions": [r for r in records if r.get("event") == "decision"],
            "browser_blocked": list(svc.browser.blocked),
            "interceptor_errors": list(svc.browser.interceptor_errors),
        }

    return {
        "case": case,
        "description": description,
        "fixture": fixture,
        "operator_text": operator_text,
        "transcript": transcript,
        "observed": observed,
    }


async def _run_all() -> list[dict[str, Any]]:
    results = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for case, description, fixture, body in CASES:
            # A fresh pair of origins per case, so evil.test hits are that
            # case's alone.
            with serve_origins() as origins:
                results.append(
                    await _run_case(case, description, fixture, body,
                                    origins.chromium_args, tmp)
                )
    return results


def _write_report(results: list[dict[str, Any]], phase: str) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    lines = [
        f"New-page regression cases, {phase} run",
        "=" * 72,
        "",
        f"allowlist: ['http://{FIXTURE_HOST}']",
        "",
        "Frozen expectations: PREDICTIONS.md amendment 2026-08-23.",
        "blocked_page entries are counted, not predicted.",
        "",
    ]
    for result in results:
        case = result["case"]
        observed = result["observed"]
        lines.append("=" * 72)
        lines.append(f"CASE {case}: {result['description']}")
        lines.append("=" * 72)
        lines.append(f"fixture:       {result['fixture']}")
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
        for field in ("pages_after", "page_urls", "service_page_url",
                      "snapshot_url", "evil_hits", "click_ok", "click_url",
                      "browser_blocked", "interceptor_errors"):
            lines.append(f"{field} = {observed[field]!r}")
        lines.append(
            f"blocked_page entries = {len(observed['blocked_page_entries'])}"
        )
        lines.append("")
        lines.append("BLOCKED_PAGE LOG ENTRIES (raw JSONL)")
        lines.append("-" * 72)
        for record in observed["blocked_page_entries"]:
            lines.append(json.dumps(record, sort_keys=True))
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
    lines.append("blocked_page entries per case: " + json.dumps(
        {r["case"]: len(r["observed"]["blocked_page_entries"]) for r in results}
    ))
    path = RESULTS / f"newpages_{phase}.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_new_page_cases_against_the_freeze() -> None:
    """Run N1 to N4 and compare every predicted field to PREDICTIONS.md.

    The phase name for the transcript comes from ``NEWPAGES_PHASE`` so the
    pre-fix baseline and the post-fix replay land in separate files.
    """
    phase = os.environ.get("NEWPAGES_PHASE", "post")
    results = asyncio.run(_run_all())
    path = _write_report(results, phase)
    differed = {
        r["case"]: _check(r["case"], r["observed"])
        for r in results
        if _check(r["case"], r["observed"])
    }
    assert not differed, (
        f"cases differing from PREDICTIONS.md amendment 2026-08-23: "
        f"{json.dumps(differed, indent=2)}\nraw transcript: {path}"
    )
