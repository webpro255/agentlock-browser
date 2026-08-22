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
        description="Provenance channel of the gated value: USER, ALLOWLIST, "
        "PAGE, MODEL, or UNCLASSIFIED.",
    )
    origin: str = Field(
        default="", description="For channel PAGE, the origin the value was read from."
    )
    decided_by: str = ""
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
