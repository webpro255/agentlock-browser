"""Human confirmation, over MCP elicitation.

The USER channel is a startup string: MCP gives a server no way to see the
operator's message, so a value the model composed or read off a page can never
trace to the operator no matter how reasonable it is.  Elicitation is the one
place in the protocol where the server can reach the human mid-call, and
probe/elicit/REPORT.md records that a real client renders the form to a person
and does not answer it itself.

So: when the gate denies on MODEL or PAGE, or allows a value it could not
classify, the server asks.  What comes back is not a verdict.  An accepted
confirmation is recorded as provenance, on the USER_CONFIRMED channel, and the
call is authorized again from the top.  The gate still decides.

Two server-side decisions live here and nowhere else: whether to ask, and
whether a previous decline still stands.  Both are logged with
``decided_by: "server:confirm"`` so neither is ever read as a gate verdict.

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from agentlock_browser.provenance import Channel

__all__ = [
    "ConfirmChoice",
    "ConfirmOutcome",
    "ConfirmationBroker",
    "Elicitor",
    "form_elicitation_available",
    "link_line",
    "needs_confirmation",
    "provenance_line",
    "redirect_line",
]

#: How much of a value is shown to the human.  The full value is what gets
#: recorded and authorized; this only bounds what a dialog has to render.
DISPLAY_LIMIT = 200

#: The two answers.  There is no deny option: the client's own decline is the
#: no, and it comes back as a distinct action rather than as a submitted form
#: (probe/elicit/REPORT.md, "The deny option inside the enum produces an
#: accept-shaped result").
CHOICES = ("allow_once", "trust_origin_session")


class ConfirmChoice(BaseModel):
    """The form the human is shown.  One field, two options."""

    choice: Literal["allow_once", "trust_origin_session"] = Field(
        description=(
            "allow_once: permit this one action. "
            "trust_origin_session: permit this action and anything else on the "
            "same origin for the rest of this session."
        ),
    )


@dataclass
class ConfirmOutcome:
    """What came back from asking, or why nothing was asked."""

    #: accepted, declined, cancelled, unavailable, cap_reached
    status: str
    choice: str = ""
    elicitation_id: str = ""
    client: str = ""
    received_at: float = 0.0
    asked: bool = False

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    def metadata(self) -> dict[str, str]:
        return {
            "elicitation_id": self.elicitation_id,
            "client": self.client,
            "received_at": str(self.received_at),
        }


class Elicitor(Protocol):
    """What the MCP layer hands in: the ability to ask, and who is being asked."""

    async def ask(self, message: str, schema: type[BaseModel]) -> Any: ...

    @property
    def client_capabilities(self) -> Any: ...

    @property
    def client_name(self) -> str: ...


def form_elicitation_available(capabilities: Any) -> bool:
    """Can this client show a form to a human?

    PREDICTIONS.md amendment 2026-08-23c, from what probe/elicit measured:
    Claude Code advertises ``{"elicitation": {}}`` with neither mode named
    while it does render forms, and the SDK's own ``ClientSession`` advertises
    both.  So modes-unspecified counts as available; url-only does not, since
    that client is saying it can open a browser tab and nothing else.
    """
    elicitation = getattr(capabilities, "elicitation", None)
    if elicitation is None:
        return False
    form = getattr(elicitation, "form", None)
    url = getattr(elicitation, "url", None)
    if form is not None:
        return True
    return url is None


def needs_confirmation(decision: Any, confirm_unclassified: bool) -> bool:
    """Is this a decision a human should be asked about?

    PREDICTIONS.md amendment 2026-08-23b item 1: a denial on MODEL or PAGE, or
    an allow the gate could not account for.  An allow on USER, USER_CONFIRMED
    or ALLOWLIST is the operator's own instruction and is never second-guessed.
    """
    channel = decision.channel
    if not decision.allowed:
        return channel in (Channel.MODEL, Channel.PAGE)
    return bool(decision.extra.get("fail_open")) and confirm_unclassified


def provenance_line(decision: Any) -> str:
    """The one line that says where the value came from."""
    if decision.channel is Channel.PAGE:
        origin = decision.origin or "an unknown origin"
        return f"from page {origin}"
    return "composed by the agent, not in your instructions"


def link_line(origin: str) -> str:
    """Where a link the page offered came from.

    Used for ``navigate(link_id)`` and for a navigation a click caused: in
    both cases the value is an href that was sitting on a page, and the page
    is what the human needs named.
    """
    return f"link on page {origin or 'an unknown origin'}"


def redirect_line(origin: str) -> str:
    """Where a redirect hop came from: the origin that served the redirect."""
    return f"redirect from {origin or 'an unknown origin'}"


def build_message(action: str, value: str, line: str) -> str:
    """Three lines: what is about to happen, the exact value, where it came from.

    The value is truncated for display only.  What gets recorded as provenance
    and handed back to the gate is the whole thing.
    """
    shown = value if len(value) <= DISPLAY_LIMIT else value[:DISPLAY_LIMIT] + "..."
    return f"Confirm {action}\nvalue:  {shown}\norigin: {line}"


@dataclass
class ConfirmationBroker:
    """Per-session confirmation state: the decline cache and the cap."""

    cap: int = 5
    #: (action, value) pairs the human declined.  A decline is an answer about
    #: this action, so the same action is not put in front of them again.  A
    #: cancellation is not an answer and is deliberately not cached.
    declined: set[tuple[str, str]] = field(default_factory=set)
    #: Declines and cancellations both count toward the cap: five dismissals
    #: is a person telling the agent to stop asking, whichever way they did it.
    refusals: int = 0

    def cached_decline(self, action: str, value: str) -> bool:
        return (action, value) in self.declined

    def cap_reached(self) -> bool:
        return self.refusals >= self.cap

    def record_refusal(self, action: str, value: str, status: str) -> None:
        self.refusals += 1
        if status == "declined":
            self.declined.add((action, value))


def build_request(action: str, value: str, line: str) -> dict[str, Any]:
    """The elicitation as it will be sent, ready to log before sending it.

    Logged first, so a request that never comes back is still on the record
    (amendment 2026-08-23b item 9).
    """
    return {
        "message": build_message(action, value, line),
        "mode": "form",
        "choices": list(CHOICES),
    }


async def send_elicitation(
    elicitor: Elicitor, message: str
) -> tuple[str, str, dict[str, Any]]:
    """Send one elicitation and return (status, choice, raw result).

    ``decline`` and ``cancel`` are kept apart: the client reports them as
    different actions, and probe/elicit measured that a real client maps a
    dismissal to cancel and its own decline button to decline.
    """
    outcome = await elicitor.ask(message, ConfirmChoice)
    raw = outcome.model_dump(by_alias=True, mode="json")
    mcp_action = raw.get("action", "")
    if mcp_action == "accept":
        data = raw.get("data") or {}
        return "accepted", str(data.get("choice", "")), raw
    if mcp_action == "decline":
        return "declined", "", raw
    return "cancelled", "", raw


def new_elicitation_id() -> str:
    """A per-request id, so a log line joins to the exchange that produced it."""
    return f"elic_{time.time_ns():x}"
