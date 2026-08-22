"""The AgentLock gate, wired in front of the browser.

Three actions are gated, following the channel rules frozen in
PREDICTIONS.md:

* ``navigate(url)``  -- url must trace to USER or ALLOWLIST.  PAGE or MODEL
  is denied.
* ``navigate(link_id)`` -- resolved server-side to a PAGE href.  Allowed only
  if the id came from the most recent snapshot.
* ``type(value)`` -- value must trace to USER.  PAGE or MODEL is denied.
* a cross-origin navigation caused by a click is authorized as
  ``navigate(url)`` with PAGE provenance, so it is denied and aborted.

Ungated in v0: snapshot, read_text, back, same-origin click.

WHO DECIDES.  The AgentLock gate decides.  This module records provenance,
calls ``gate.authorize()``, and reports what came back.  It never overrides a
verdict.  The one place it acts before the gate is the link-id freshness
rule, which AgentLock has no way to express -- that denial is marked
``decided_by: "server:link_freshness"`` in the log so it is never mistaken
for a gate verdict.  See NEEDS.md.

Channel *labels* on an allowed call (USER vs ALLOWLIST) are this layer's
advisory attribution, not an enforcement decision: AgentLock collapses both
into AUTHORITATIVE and does not report which entry a grant rested on.

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import dataclasses
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

from agentlock import (
    AgentLockPermissions,
    AuthorizationGate,
    InMemoryAuditBackend,
    LineagePolicyConfig,
    ReceiptSigner,
)

from agentlock_browser.config import BrowserConfig, origin_of
from agentlock_browser.log import DecisionLog
from agentlock_browser.provenance import Channel, ProvenanceLedger

__all__ = ["BrowserGate", "Decision", "TOOL_NAVIGATE", "TOOL_NAVIGATE_LINK", "TOOL_TYPE"]

TOOL_NAVIGATE = "browser.navigate"
TOOL_NAVIGATE_LINK = "browser.navigate_link"
TOOL_TYPE = "browser.type"

#: Separate AgentLock sessions per gated action.  Not cosmetic: AgentLock
#: resolves exactly one context authority per entry per session, so a single
#: session cannot hold "ALLOWLIST is trusted for navigate but not for type".
#: The type channel's authoritative context is USER only.
USER_NAV = "agentlock-browser:navigate"
USER_TYPE = "agentlock-browser:type"


@dataclass
class Decision:
    """One authorization outcome, as recorded."""

    action: str
    tool: str
    allowed: bool
    decision: str
    channel: Channel
    reason: str = ""
    detail: str = ""
    origin: str = ""
    value: str = ""
    decided_by: str = "agentlock_gate"
    audit_id: str = ""
    receipt: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    grant_basis: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_log_fields(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["channel"] = str(self.channel)
        extra = d.pop("extra")
        d.update(extra)
        return d


def _canon(url: str) -> str:
    """Canonicalize a URL for advisory containment tests: drop scheme, a
    leading www., and trailing punctuation.  Mirrors AgentLock's own URL
    canonicalization closely enough for labelling; it never gates anything."""
    t = url.strip().lower()
    t = re.sub(r"^https?://", "", t)
    t = re.sub(r"^www\.", "", t)
    return t.rstrip("/.,;:)!?\"'")


class BrowserGate:
    """Registers the gated browser tools with AgentLock and authorizes calls."""

    def __init__(
        self,
        config: BrowserConfig,
        gate: AuthorizationGate | None = None,
        log: DecisionLog | None = None,
    ) -> None:
        self.config = config
        self.audit_backend = InMemoryAuditBackend()
        self.gate = gate or AuthorizationGate(
            audit_backend=self.audit_backend,
            receipt_signer=ReceiptSigner(
                "hmac-sha256",
                signing_key=config.signing_key or secrets.token_bytes(32),
                key_id="agentlock-browser",
            ),
        )
        self.log = log or DecisionLog(config.log_path)
        self.ledger = ProvenanceLedger(self.gate)

        self._register_tools()
        self.nav_session = self.gate.create_session(USER_NAV, config.role)
        self.type_session = self.gate.create_session(USER_TYPE, config.role)

        # Seed the trusted channels.  Order matters only for the log.
        if config.allowlist:
            for origin in config.allowlist:
                for entry in self.ledger.record_allowlist(
                    [self.nav_session.session_id], origin
                ):
                    self._log_provenance(entry, note="startup allowlist")
        if config.operator_text:
            self.seed_operator_text(config.operator_text)

    # -- registration ------------------------------------------------------

    def _lineage_policy(self) -> LineagePolicyConfig:
        """Parameter and novel lineage, both denying.

        ``enabled=False`` switches off the coarse *session*-taint write gate:
        v0 gates each value on its own provenance, not on whether the session
        has read anything untrusted at all.  Reading a hostile page must not
        stop the operator from navigating somewhere they asked for.
        """
        return LineagePolicyConfig(
            enabled=False,
            param_lineage_enabled=True,
            param_lineage_action="deny",
            novel_lineage_enabled=True,
            novel_lineage_action="deny",
        )

    def _register_tools(self) -> None:
        gated = AgentLockPermissions(
            risk_level="high",
            requires_auth=False,
            allowed_roles=[self.config.role],
            lineage_policy=self._lineage_policy(),
        )
        self.gate.register_tool(TOOL_NAVIGATE, gated)
        self.gate.register_tool(TOOL_TYPE, gated)
        # navigate(link_id) carries no attacker-chosen value: the id is
        # resolved server-side against the most recent snapshot, and the href
        # never leaves the server.  Lineage would deny every link (the href is
        # PAGE by construction), which is not the rule PREDICTIONS.md states.
        self.gate.register_tool(
            TOOL_NAVIGATE_LINK,
            AgentLockPermissions(
                risk_level="medium",
                requires_auth=False,
                allowed_roles=[self.config.role],
            ),
        )

    # -- provenance --------------------------------------------------------

    @property
    def _all_sessions(self) -> list[str]:
        return [self.nav_session.session_id, self.type_session.session_id]

    def seed_operator_text(self, text: str) -> None:
        """Record the operator's message text on the USER channel."""
        for entry in self.ledger.record_user_text(self._all_sessions, text):
            self._log_provenance(entry, note="operator text")

    def record_page_content(self, origin: str, text: str) -> None:
        """Record content read from a page on the PAGE(origin) channel."""
        for entry in self.ledger.record_page(self._all_sessions, origin, text):
            self._log_provenance(entry, note="page read")

    def _log_provenance(self, entry: Any, note: str = "") -> None:
        self.log.write(
            "provenance",
            provenance_id=entry.provenance_id,
            channel=str(entry.channel),
            origin=entry.origin,
            session_id=entry.session_id,
            content_sha256=ProvenanceLedger.hash_content(entry.content),
            content_len=len(entry.content),
            note=note,
        )

    # -- authorization -----------------------------------------------------

    def _audit_metadata(self, audit_id: str) -> dict[str, Any]:
        for record in self.audit_backend.query(limit=200):
            if record.audit_id == audit_id:
                return dict(record.metadata or {})
        return {}

    def _receipt_dict(self, result: Any) -> dict[str, Any] | None:
        if result.receipt is None:
            return None
        return dataclasses.asdict(result.receipt)

    def _channel_from_evidence(self, evidence: dict[str, Any] | None) -> tuple[Channel, str]:
        """The channel the gate's own denial evidence points at."""
        if not evidence:
            return Channel.UNCLASSIFIED, ""
        if evidence.get("gate") == "param_lineage":
            pid = evidence.get("untrusted_provenance_id") or ""
            if not pid:
                ref = str(evidence.get("untrusted_source_ref", ""))
                pid = ref.split(":", 1)[1] if ":" in ref else ""
            entry = self.ledger.get(pid)
            if entry is not None:
                return entry.channel, entry.origin
            return Channel.PAGE, ""
        if evidence.get("gate") == "novel_lineage":
            return Channel.MODEL, ""
        return Channel.UNCLASSIFIED, ""

    def _traces_to_user(self, value: str) -> bool:
        """Advisory: does this value appear in the operator's own message?

        Labelling only.  A false answer never allows anything and never denies
        anything -- the gate has already decided by the time this is asked.
        """
        text = self.ledger.user_text().lower()
        if not text or not value:
            return False
        return value.strip().lower() in text or _canon(value) in text

    def _authorize(
        self,
        tool: str,
        user_id: str,
        parameters: dict[str, Any],
        action: str,
        *,
        value: str,
        expected_channel: Channel | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Decision:
        result = self.gate.authorize(
            tool,
            user_id=user_id,
            role=self.config.role,
            parameters=parameters,
            is_external=True,
        )
        meta = self._audit_metadata(result.audit_id)
        evidence = meta.get("lineage_evidence")
        grant_basis = meta.get("grant_basis")

        if result.allowed:
            channel = expected_channel or (
                Channel.USER if self._traces_to_user(value) else Channel.UNCLASSIFIED
            )
            origin = ""
        else:
            channel, origin = self._channel_from_evidence(evidence)

        decision = Decision(
            action=action,
            tool=tool,
            allowed=result.allowed,
            decision=result.decision.value,
            channel=channel,
            reason=(result.denial or {}).get("reason", ""),
            detail=(result.denial or {}).get("detail", ""),
            origin=origin,
            value=value,
            audit_id=result.audit_id,
            receipt=self._receipt_dict(result),
            evidence=evidence,
            grant_basis=grant_basis,
            extra=dict(extra or {}),
        )
        if result.allowed and channel is Channel.UNCLASSIFIED:
            # The gate found nothing to deny on, but no channel accounts for
            # the value either.  Named, not hidden.  See NEEDS.md item 3.
            decision.extra["fail_open"] = True
        self.log.write("decision", **decision.as_log_fields())
        return decision

    # -- the three gated actions ------------------------------------------

    def authorize_navigate_url(self, url: str, *, cause: str = "tool") -> Decision:
        """Authorize ``navigate(url)``.

        ``cause="tool"`` is a direct call from the model.  ``cause`` is also
        the hook for an intercepted click-driven navigation, which is
        authorized identically but never materializes the allowlist: the value
        came off a page, not out of the operator's configuration.
        """
        expected: Channel | None = None
        if cause == "tool":
            if self._traces_to_user(url):
                expected = Channel.USER
            elif self.config.is_allowlisted(url):
                # The operator allowlisted this ORIGIN; AgentLock's lineage
                # engine matches whole tokens, so an allowlisted origin does
                # not cover a path under it.  Record the concrete URL as
                # trusted configuration so the gate is deciding on the same
                # facts the operator stated.  It is written to the log as its
                # own provenance entry, so an auditor sees exactly why the
                # grant rested where it did.
                for entry in self.ledger.record_allowlist(
                    [self.nav_session.session_id], url
                ):
                    self._log_provenance(entry, note="allowlist materialized for url")
                expected = Channel.ALLOWLIST

        return self._authorize(
            TOOL_NAVIGATE,
            USER_NAV,
            {"url": url},
            action="navigate_url" if cause == "tool" else "intercepted_navigation",
            value=url,
            expected_channel=expected,
            extra={"cause": cause, "target_origin": origin_of(url)},
        )

    def authorize_navigate_link(
        self, link_id: str, href: str | None, *, fresh: bool, page_origin: str
    ) -> Decision:
        """Authorize ``navigate(link_id)``.

        The freshness rule is enforced here, before the gate: AgentLock has no
        way to express "this identifier must have come from the most recent
        tool output".  A stale or unknown id is denied by the server and
        recorded as such.  A fresh id then goes to the gate for the rest.
        """
        if not fresh or href is None:
            decision = Decision(
                action="navigate_link",
                tool=TOOL_NAVIGATE_LINK,
                allowed=False,
                decision="deny",
                channel=Channel.PAGE,
                reason="stale_or_unknown_link_id",
                detail=(
                    f"link_id '{link_id}' was not returned by the most recent "
                    f"snapshot. Take a fresh snapshot and use an id from it."
                ),
                origin=page_origin,
                value=link_id,
                decided_by="server:link_freshness",
                receipt=self._sign_server_receipt(
                    TOOL_NAVIGATE_LINK, "deny", "stale_or_unknown_link_id", link_id
                ),
                extra={"href": None},
            )
            self.log.write("decision", **decision.as_log_fields())
            return decision

        return self._authorize(
            TOOL_NAVIGATE_LINK,
            USER_NAV,
            {"link_id": link_id},
            action="navigate_link",
            value=link_id,
            expected_channel=Channel.PAGE,
            extra={"href": href, "target_origin": origin_of(href)},
        )

    def authorize_type(self, element_id: str, value: str) -> Decision:
        """Authorize ``type(value)``.  Only USER-traced values may be typed."""
        return self._authorize(
            TOOL_TYPE,
            USER_TYPE,
            {"value": value},
            action="type",
            value=value,
            extra={"element_id": element_id},
        )

    # -- receipts for decisions the gate did not make ----------------------

    def _sign_server_receipt(
        self, tool: str, decision: str, reason: str, value: str
    ) -> dict[str, Any] | None:
        """Sign a receipt for a server-side denial with AgentLock's signer.

        Used only by the link-id freshness rule.  ``decided_by`` on the record
        says who decided; the signature says the record was not altered after.
        """
        signer = self.gate.receipt_signer
        if signer is None:
            return None
        from agentlock import SignedReceipt

        receipt = SignedReceipt(
            decision=decision,
            tool_name=tool,
            user_id=USER_NAV,
            role=self.config.role,
            parameters_hash=ProvenanceLedger.hash_content(value),
            reason=reason,
        )
        signer.sign(receipt)
        return dataclasses.asdict(receipt)
