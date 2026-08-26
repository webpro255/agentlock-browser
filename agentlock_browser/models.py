"""Structured tool results.

Every tool returns one of these.  They exist so that the MCP layer emits
``structuredContent`` with a declared ``outputSchema`` on every call -- the
opposite of the single untyped text blob the probe recorded upstream
(probe/REPORT.md, "Result envelope -- uniform across every call").

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "GateDecision",
    "Element",
    "SnapshotResult",
    "TextBlock",
    "ReadTextResult",
    "NavigateResult",
    "ClickResult",
    "TypeResult",
    "BackResult",
]


class GateDecision(BaseModel):
    """What the gate decided, and on what provenance."""

    allowed: bool
    decision: str
    reason: str = ""
    detail: str = ""
    channel: str = Field(
        default="",
        description="Provenance channel of the gated value: USER, "
        "USER_CONFIRMED, ALLOWLIST, PAGE, MODEL, or UNCLASSIFIED.",
    )
    origin: str = Field(
        default="", description="For channel PAGE, the origin the value was read from."
    )
    decided_by: str = ""
    confirmation: str = Field(
        default="",
        description="Set when a human was asked about this action: accepted, "
        "declined, cancelled, unavailable, or cap_reached.",
    )
    fail_open: bool = Field(
        default=False,
        description="True when the gate allowed the call but no channel "
        "accounts for the value: it carried no token distinctive enough to "
        "classify. A grant, not a denial, and named as one.",
    )
    target: str = Field(
        default="",
        description="For a navigation the page caused, the URL it was trying "
        "to reach. The operator is asked about it before the call returns; "
        "'confirmation' says what they answered.",
    )
    receipt_id: str = ""
    audit_id: str = ""


class Element(BaseModel):
    id: str = Field(description="Stable for this page load; regenerated on navigation.")
    role: str
    name: str
    text: str
    href: str


class SnapshotResult(BaseModel):
    origin: str
    url: str
    title: str
    elements: list[Element]


class TextBlock(BaseModel):
    id: str
    text: str


class ReadTextResult(BaseModel):
    origin: str
    blocks: list[TextBlock]


class NavigateResult(BaseModel):
    ok: bool
    gate: GateDecision
    origin: str = ""
    url: str = ""
    title: str = ""
    error: str = ""
    blocked: list[GateDecision] = Field(
        default_factory=list,
        description="Navigations this call triggered that the gate denied and "
        "the browser refused, such as a cross-origin redirect.",
    )


class ClickResult(BaseModel):
    ok: bool
    element_id: str
    origin: str = ""
    url: str = ""
    title: str = ""
    blocked: list[GateDecision] = Field(
        default_factory=list,
        description="Cross-origin navigations this click triggered that the "
        "gate denied and the browser aborted.",
    )
    error: str = ""


class TypeResult(BaseModel):
    ok: bool
    element_id: str
    gate: GateDecision
    origin: str = ""
    url: str = ""
    error: str = ""


class BackResult(BaseModel):
    ok: bool
    origin: str = ""
    url: str = ""
    title: str = ""
