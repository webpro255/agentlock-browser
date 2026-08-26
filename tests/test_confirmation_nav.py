"""Navigation-confirmation regression cases EM1 to EM10.

probe/manual/REPORT.md recorded the two holes these cover: a
``navigate(link_id)`` to an origin that is neither the current page's nor on
the allowlist went through on freshness alone, and an intercepted cross-origin
hop was denied with no way for the human to say yes short of retyping the URL.

Driven through a real MCP client session over stdio, so the elicitation
crosses the wire.  The client's ``elicitation_callback`` is scripted per case
and every line it produces is tagged ``SCRIPTED_HUMAN``.

Three local origins, all real: ``fixture.test`` (allowlisted), ``evil.test``
and ``third.test``.  Each records what actually reached it, so a chain that
leaves the second origin can be told from one that stopped there.

Expectations are the ones frozen in PREDICTIONS.md, amendment 2026-08-23e.
The raw transcript is written to
``tests/results/confirmation_nav_<phase>.txt`` before any assertion runs.

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
from urllib.parse import urlsplit

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agentlock_browser.log import DecisionLog
from tests.harness import (
    EVIL_HOST,
    FIXTURE_HOST,
    RESULTS,
    THIRD_HOST,
    evil_hits,
    serve_origins,
    server_command,
    third_hits,
)
from tests.test_confirmation import SCRIPTED_HUMAN, ScriptedHuman

FIXTURE = f"http://{FIXTURE_HOST}"
EVIL_NEXT = f"http://{EVIL_HOST}/next"
EVIL_ROOT = f"http://{EVIL_HOST}/"
EVIL_LANDED = f"http://{EVIL_HOST}/landed"
THIRD_B = f"http://{THIRD_HOST}/b"

#: Carries a distinctive token, so novel lineage has an authoritative baseline
#: and the MODEL channel stays active (NEEDS.md item 3(b)).
OPERATOR_TEXT = f"research the pages on http://{FIXTURE_HOST}/index.html"


# -- helpers --------------------------------------------------------------


def _link_id(snapshot: dict[str, Any], href_contains: str) -> str:
    for element in snapshot.get("elements", []):
        if element.get("role") == "link" and href_contains in element.get("href", ""):
            return element["id"]
    raise AssertionError(f"no link containing {href_contains!r} in {snapshot}")


# -- case bodies ----------------------------------------------------------


async def _link_offsite(call: Any) -> None:
    snap = await call("snapshot", {}, tag="setup")
    await call("navigate", {"link_id": _link_id(snap, EVIL_HOST)}, tag="gated")


async def _link_same_origin(call: Any) -> None:
    snap = await call("snapshot", {}, tag="setup")
    await call("navigate", {"link_id": _link_id(snap, "/article.html")}, tag="gated")


async def _click_offsite(call: Any) -> None:
    snap = await call("snapshot", {}, tag="setup")
    await call("click", {"element_id": _link_id(snap, EVIL_HOST)}, tag="gated")


async def _navigate_redirect(call: Any) -> None:
    await call("navigate", {"url": f"{FIXTURE}/redirect"}, tag="gated")


async def _navigate_chain(call: Any) -> None:
    await call("navigate", {"url": f"{FIXTURE}/chain"}, tag="gated")


async def _navigate_same_redirect(call: Any) -> None:
    await call("navigate", {"url": f"{FIXTURE}/same-redirect"}, tag="gated")


async def _meta_delayed(call: Any) -> None:
    # The tool returns before the refresh fires; the wait is what lets it.
    await call("read_text", {}, tag="gated")
    await asyncio.sleep(5)


async def _cap_then_click(call: Any) -> None:
    for n in range(1, 6):
        await call("navigate", {"url": f"http://{EVIL_HOST}/step-{n}?id=7731"},
                   tag=f"decline{n}")
    snap = await call("snapshot", {}, tag="setup")
    await call("click", {"element_id": _link_id(snap, EVIL_HOST)}, tag="gated")


CASES: list[dict[str, Any]] = [
    {"case": "EM1", "description": "link_id off the allowlist, human allows once",
     "fixture": "t2_offsite.html", "answers": ["allow_once"], "body": _link_offsite},
    {"case": "EM2", "description": "link_id off the allowlist, human declines",
     "fixture": "t2_offsite.html", "answers": ["decline"], "body": _link_offsite},
    {"case": "EM3", "description": "link_id on the current origin",
     "fixture": "t2_offsite.html", "answers": [], "body": _link_same_origin},
    {"case": "EM4", "description": "click offsite link, human allows once",
     "fixture": "t4_escape.html", "answers": ["allow_once"], "body": _click_offsite},
    {"case": "EM5", "description": "click offsite link, human declines",
     "fixture": "t4_escape.html", "answers": ["decline"], "body": _click_offsite},
    {"case": "EM6", "description": "cross-origin 302, human allows once",
     "fixture": "index.html", "answers": ["allow_once"], "body": _navigate_redirect},
    {"case": "EM7", "description": "two-hop chain, human allows both",
     "fixture": "index.html", "answers": ["allow_once", "allow_once"],
     "body": _navigate_chain},
    {"case": "EM8", "description": "same-origin 302",
     "fixture": "index.html", "answers": [], "body": _navigate_same_redirect},
    {"case": "EM9", "description": "meta refresh after the tool returned",
     "fixture": "meta_delayed.html", "answers": [], "body": _meta_delayed},
    {"case": "EM10", "description": "five declines, then a click",
     "fixture": "t4_escape.html", "answers": ["decline"] * 5,
     "body": _cap_then_click},
]


#: PREDICTIONS.md amendment 2026-08-23e, one entry per case.  A key absent
#: from an entry is not constrained by the freeze.
#:
#: ``evil_hit_paths`` and ``third_hit_paths`` are path components, without the
#: query string, matching how the freeze writes them.
FROZEN: dict[str, dict[str, Any]] = {
    "EM1": {
        "elicit_requests_logged": 1,
        "elicit_message_contains": f"link on page http://{FIXTURE_HOST}",
        "gated": {"allowed": True, "channel": "USER_CONFIRMED"},
        "final_url": EVIL_NEXT,
        "evil_hit_paths": ["/next"], "third_hit_paths": [],
    },
    "EM2": {
        "elicit_requests_logged": 1,
        "gated": {"allowed": False, "confirmation": "declined"},
        "final_url_host": FIXTURE_HOST,
        "evil_hit_paths": [], "third_hit_paths": [],
    },
    "EM3": {
        "elicit_requests_logged": 0,
        "gated": {"allowed": True},
        "final_url_host": FIXTURE_HOST,
        "evil_hit_paths": [], "third_hit_paths": [],
    },
    "EM4": {
        "elicit_requests_logged": 1,
        "intercepted_denials_logged_min": 1,
        "post_confirm_decision": {"allowed": True, "channel": "USER_CONFIRMED"},
        "gated": {"ok": True},
        "final_url": EVIL_ROOT,
        "evil_hit_paths": ["/"], "third_hit_paths": [],
    },
    "EM5": {
        "elicit_requests_logged": 1,
        "intercepted_denials_logged_min": 1,
        "gated": {"ok": False},
        "final_url_host": FIXTURE_HOST,
        "evil_hit_paths": [], "third_hit_paths": [],
    },
    "EM6": {
        "elicit_requests_logged": 1,
        "elicit_message_contains": f"redirect from http://{FIXTURE_HOST}",
        "intercepted_denials_logged_min": 1,
        "post_confirm_decision": {"allowed": True, "channel": "USER_CONFIRMED"},
        "final_url": EVIL_LANDED,
        "evil_hit_paths": ["/landed"], "third_hit_paths": [],
    },
    "EM7": {
        "elicit_requests_logged": 2,
        "final_url": THIRD_B,
        "evil_hit_paths": ["/a"], "third_hit_paths": ["/b"],
    },
    "EM8": {
        "elicit_requests_logged": 0,
        "gated": {"allowed": True},
        "final_url_host": FIXTURE_HOST,
        "evil_hit_paths": [], "third_hit_paths": [],
    },
    "EM9": {
        "elicit_requests_logged": 0,
        "intercepted_denials_logged_min": 1,
        "final_url_host": FIXTURE_HOST,
        "evil_hit_paths": [], "third_hit_paths": [],
    },
    "EM10": {
        "elicit_requests_logged": 5,
        "gated": {"blocked_confirmation": "cap_reached"},
        "final_url_host": FIXTURE_HOST,
        "evil_hit_paths": [], "third_hit_paths": [],
    },
}


def _gate_of(step: dict[str, Any]) -> dict[str, Any]:
    payload = step.get("result")
    if not isinstance(payload, dict):
        return {}
    gate = payload.get("gate")
    return gate if isinstance(gate, dict) else {}


def _blocked(step: dict[str, Any]) -> dict[str, Any]:
    payload = step.get("result")
    if not isinstance(payload, dict):
        return {}
    entries = payload.get("blocked") or []
    return entries[0] if entries else {}


def _check(case: str, observed: dict[str, Any]) -> list[str]:
    """Fields that differ from the freeze.  Empty list means exact match."""
    frozen = FROZEN[case]
    diffs: list[str] = []

    if "elicit_requests_logged" in frozen:
        want, got = frozen["elicit_requests_logged"], observed["elicit_requests_logged"]
        if got != want:
            diffs.append(f"elicit_requests_logged: frozen {want!r}, observed {got!r}")

    if "elicit_message_contains" in frozen:
        want = frozen["elicit_message_contains"]
        if not any(want in m for m in observed["elicit_messages"]):
            diffs.append(
                f"elicit_message_contains: frozen {want!r}, observed messages "
                f"{observed['elicit_messages']!r}"
            )

    if "intercepted_denials_logged_min" in frozen:
        want = frozen["intercepted_denials_logged_min"]
        got = observed["intercepted_denials_logged"]
        if got < want:
            diffs.append(
                f"intercepted_denials_logged: frozen at least {want!r}, "
                f"observed {got!r}"
            )

    if "post_confirm_decision" in frozen:
        found = observed["post_confirm_decisions"]
        if not found:
            diffs.append(
                f"post_confirm_decision: frozen {frozen['post_confirm_decision']!r}, "
                f"observed no decision with cause 'post_confirm'"
            )
        else:
            for field, want in frozen["post_confirm_decision"].items():
                got = found[0].get(field)
                if got != want:
                    diffs.append(
                        f"post_confirm_decision.{field}: frozen {want!r}, "
                        f"observed {got!r}"
                    )

    if "gated" in frozen:
        step = observed["steps"].get("gated")
        if step is None:
            diffs.append("gated: frozen a step, observed none")
        else:
            gate = _gate_of(step)
            blocked = _blocked(step)
            payload = step.get("result") or {}
            for field, want in frozen["gated"].items():
                if field == "ok":
                    got: Any = payload.get("ok")
                elif field == "blocked_confirmation":
                    got = blocked.get("confirmation")
                else:
                    got = gate.get(field)
                if got != want:
                    diffs.append(f"gated.{field}: frozen {want!r}, observed {got!r}")

    if "final_url" in frozen and observed["final_url"] != frozen["final_url"]:
        diffs.append(
            f"final_url: frozen {frozen['final_url']!r}, "
            f"observed {observed['final_url']!r}"
        )
    if "final_url_host" in frozen:
        want = frozen["final_url_host"]
        if want not in observed["final_url"]:
            diffs.append(
                f"final_url: frozen host {want!r}, observed {observed['final_url']!r}"
            )

    for key in ("evil_hit_paths", "third_hit_paths"):
        if key in frozen and observed[key] != frozen[key]:
            diffs.append(f"{key}: frozen {frozen[key]!r}, observed {observed[key]!r}")

    return diffs


# -- running --------------------------------------------------------------


async def _run_case(spec: dict[str, Any], origins: Any, tmp: Path) -> dict[str, Any]:
    case = spec["case"]
    log_path = tmp / f"confirmation_nav_{case}.jsonl"
    if log_path.exists():
        log_path.unlink()
    config_path = tmp / f"config_{case}.json"
    config = {
        "allowlist": [FIXTURE],
        "operator_text": OPERATOR_TEXT,
        "log_path": str(log_path),
        "headless": True,
        "chromium_args": list(origins.chromium_args),
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")

    env = dict(os.environ)
    env["AGENTLOCK_BROWSER_CONFIG"] = str(config_path)
    env.pop("OPENAI_API_KEY", None)

    human = ScriptedHuman(spec["answers"])
    page_url = f"{FIXTURE}/{spec['fixture']}"
    steps: dict[str, dict[str, Any]] = {}
    transcript: list[dict[str, Any]] = []
    final_url = ""

    params = StdioServerParameters(command=server_command(), args=[], env=env)
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(
            reader, writer, elicitation_callback=human
        ) as session:
            await session.initialize()

            async def call(name: str, arguments: dict[str, Any],
                           *, tag: str) -> dict[str, Any]:
                nonlocal final_url
                result = await session.call_tool(name, arguments)
                payload = result.structured_content
                step = {"tag": tag, "tool": name, "arguments": arguments,
                        "is_error": result.is_error, "result": payload}
                transcript.append(step)
                steps[tag] = step
                if isinstance(payload, dict) and payload.get("url"):
                    final_url = payload["url"]
                return payload if isinstance(payload, dict) else {}

            await call("navigate", {"url": page_url}, tag="setup_navigate")
            await spec["body"](call)

            # Read the page url back after the body, so a navigation that
            # settled late (EM9) is reflected in what the case records.
            tail = await call("snapshot", {}, tag="final_snapshot")
            if tail.get("url"):
                final_url = tail["url"]

    records = DecisionLog(log_path).read()
    elicit_requests = [r for r in records if r.get("event") == "elicit_request"]
    decisions = [r for r in records if r.get("event") == "decision"]
    evil = evil_hits()
    third = third_hits()
    observed = {
        "elicit_requests_logged": len(elicit_requests),
        "elicit_messages": [str(r.get("message", "")) for r in elicit_requests],
        "scripted_human_calls": human.calls,
        "intercepted_denials_logged": len([
            d for d in decisions
            if d.get("action") == "intercepted_navigation" and not d.get("allowed")
        ]),
        "post_confirm_decisions": [
            d for d in decisions if d.get("cause") == "post_confirm"
        ],
        "steps": steps,
        "final_url": final_url,
        "evil_hits": evil,
        "third_hits": third,
        "evil_hit_paths": [urlsplit(h).path for h in evil],
        "third_hit_paths": [urlsplit(h).path for h in third],
        "decisions": decisions,
        "log_events": [r.get("event") for r in records],
    }
    return {
        "case": case,
        "description": spec["description"],
        "fixture": spec["fixture"],
        "scripted_answers": spec["answers"],
        "transcript": transcript,
        "exchanges": human.exchanges,
        "observed": observed,
    }


async def _run_all() -> list[dict[str, Any]]:
    results = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for spec in CASES:
            with serve_origins() as origins:
                results.append(await _run_case(spec, origins, tmp))
    return results


def _write_report(results: list[dict[str, Any]], phase: str) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    lines = [
        f"Navigation-confirmation regression cases, {phase} run",
        "=" * 72,
        "",
        f"allowlist:     ['{FIXTURE}']",
        f"operator_text: {OPERATOR_TEXT!r}",
        f"origins:       {FIXTURE}, http://{EVIL_HOST}, http://{THIRD_HOST}",
        "",
        "Frozen expectations: PREDICTIONS.md amendment 2026-08-23e. Every",
        f"elicitation answer was produced by the callback tagged {SCRIPTED_HUMAN};",
        "no human was in the loop.",
        "",
    ]
    for result in results:
        case = result["case"]
        observed = result["observed"]
        lines.append("=" * 72)
        lines.append(f"CASE {case}: {result['description']}")
        lines.append("=" * 72)
        lines.append(f"fixture:          {result['fixture']}")
        lines.append(f"scripted answers: {result['scripted_answers']!r}")
        lines.append("")
        lines.append("TOOL CALLS AND RAW RESULTS")
        lines.append("-" * 72)
        for step in result["transcript"]:
            lines.append(
                f"-> [{step['tag']}] {step['tool']} "
                f"{json.dumps(step['arguments'], sort_keys=True)}"
            )
            lines.append(json.dumps(step["result"], indent=2, sort_keys=True,
                                    default=str))
            lines.append("")
        lines.append(f"{SCRIPTED_HUMAN} EXCHANGES")
        lines.append("-" * 72)
        if result["exchanges"]:
            for exchange in result["exchanges"]:
                lines.append(json.dumps(exchange, sort_keys=True, default=str))
        else:
            lines.append("(none)")
        lines.append("")
        lines.append("OBSERVED")
        lines.append("-" * 72)
        for field in ("elicit_requests_logged", "scripted_human_calls",
                      "elicit_messages", "intercepted_denials_logged",
                      "final_url", "evil_hits", "third_hits",
                      "evil_hit_paths", "third_hit_paths", "log_events"):
            lines.append(f"{field} = {observed[field]!r}")
        lines.append(
            f"post_confirm_decisions = {len(observed['post_confirm_decisions'])}"
        )
        lines.append("")
        lines.append("DECISION LOG (raw JSONL)")
        lines.append("-" * 72)
        for record in observed["decisions"]:
            lines.append(json.dumps(record, sort_keys=True, default=str))
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
    path = RESULTS / f"confirmation_nav_{phase}.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_navigation_confirmation_cases_against_the_freeze() -> None:
    """Run EM1 to EM10 and compare every frozen field to PREDICTIONS.md."""
    phase = os.environ.get("CONFIRMATION_NAV_PHASE", "post")
    results = asyncio.run(_run_all())
    path = _write_report(results, phase)
    differed = {
        r["case"]: _check(r["case"], r["observed"])
        for r in results
        if _check(r["case"], r["observed"])
    }
    assert not differed, (
        f"cases differing from PREDICTIONS.md amendment 2026-08-23e: "
        f"{json.dumps(differed, indent=2)}\nraw transcript: {path}"
    )
