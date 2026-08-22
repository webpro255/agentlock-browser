"""The six tools, as plain async methods.

server.py exposes these over MCP; the tests drive them directly.  Keeping the
tool bodies out of the MCP layer is what lets T5 run without a model, a client,
or a transport in the loop.

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import time

from agentlock_browser.browser import BrowserSession
from agentlock_browser.config import BrowserConfig, origin_of
from agentlock_browser.confirm import (
    ConfirmationBroker,
    Elicitor,
    build_request,
    form_elicitation_available,
    needs_confirmation,
    new_elicitation_id,
    provenance_line,
    send_elicitation,
)
from agentlock_browser.gate import BrowserGate, Decision
from agentlock_browser.models import (
    BackResult,
    ClickResult,
    Element,
    GateDecision,
    NavigateResult,
    ReadTextResult,
    SnapshotResult,
    TextBlock,
    TypeResult,
)
from agentlock_browser.provenance import Channel

__all__ = ["BrowserService"]


def _as_model(decision: Decision) -> GateDecision:
    receipt = decision.receipt or {}
    return GateDecision(
        allowed=decision.allowed,
        decision=decision.decision,
        reason=decision.reason,
        detail=decision.detail,
        channel=str(decision.channel),
        origin=decision.origin,
        decided_by=decision.decided_by,
        confirmation=decision.confirmation,
        # The same value the log line carries. NEEDS.md item 3 says a
        # fail-open grant is returned to the caller in the words the log
        # uses; until now it was only in the log.
        fail_open=bool(decision.extra.get("fail_open", False)),
        # A navigation the page caused names its target, so the model can ask
        # for it by URL and have the operator confirm it. The click itself
        # stays ungated and the interceptor's deny path is unchanged.
        target=(
            decision.value
            if decision.action == "intercepted_navigation"
            else ""
        ),
        receipt_id=str(receipt.get("receipt_id", "")),
        audit_id=decision.audit_id,
    )


class BrowserService:
    """One browser, one gate, one decision log."""

    def __init__(self, config: BrowserConfig, gate: BrowserGate | None = None) -> None:
        self.config = config
        self.gate = gate or BrowserGate(config)
        self.browser = BrowserSession(config)
        self._intercepted: list[Decision] = []
        self.confirmations = ConfirmationBroker(cap=config.confirm_cap)

    async def start(self) -> None:
        await self.browser.start()
        self.browser.on_cross_origin = self._authorize_intercepted
        self.browser.on_new_page = self._record_blocked_page

    async def close(self) -> None:
        await self.browser.close()

    async def __aenter__(self) -> BrowserService:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # -- interception hook -------------------------------------------------

    def _authorize_intercepted(
        self, target: str, origin: str, redirected_from: str | None = None
    ) -> bool:
        """A navigation tried to leave the origin it was authorized for.

        Reached by a click, a meta refresh, a script assigning location, or an
        HTTP redirect served by that origin.  PREDICTIONS.md: treated as
        navigate(url) with PAGE provenance.  The allowlist is deliberately not
        consulted -- this value came off a page, not out of the operator's
        configuration.

        The target is recorded as a PAGE(origin) write BEFORE authorize(), so
        the gate decides against provenance that actually exists.  Without it a
        target the session never read traces to neither the authoritative nor
        the untrusted context and is denied as novel, which only holds while
        the authoritative baseline carries a distinctive token.  Recording it
        first makes the denial parameter lineage instead, and the receipt then
        cites the origin the target came from.

        ``redirected_from`` is the CDP request id of the request this one
        redirected from, when there was one.  It is recorded so a redirect
        denial can be joined back to the request that caused it.
        """
        self.gate.record_page_content(origin, target)
        extra: dict[str, str] = {"caused_by_origin": origin}
        if redirected_from:
            extra["redirected_from_request_id"] = redirected_from
        decision = self.gate.authorize_navigate_url(
            target, cause="intercepted_click", extra=extra
        )
        self._intercepted.append(decision)
        return decision.allowed

    def _record_blocked_page(self, url: str) -> None:
        """A new top-level page appeared and was closed.

        Not a gate decision: nothing was authorized or denied, so this is
        logged under its own event rather than as a decision the gate made.
        Chromium is launched with the new-page flag, so reaching this at all
        means the flag let something through.
        """
        self.gate.log.write("blocked_page", url=url, decided_by="server:one_tab")

    # -- human confirmation ------------------------------------------------

    async def _confirm(
        self,
        decision: Decision,
        elicitor: Elicitor | None,
        *,
        action: str,
        value: str,
    ) -> tuple[Decision | None, str]:
        """Ask the human about a decision the gate could not settle for them.

        Returns ``(None, elicitation_id)`` when the call should be authorized
        again (the human accepted), or ``(denial, elicitation_id)`` to return
        to the model.  Never returns an allow: an accepted confirmation is
        provenance, and the gate decides again from the top with it in hand.
        """
        if elicitor is None or not form_elicitation_available(
            elicitor.client_capabilities
        ):
            self.gate.log_confirmation(
                "confirmation_skipped", action=action, value=value,
                status="unavailable",
            )
            return self.gate.refuse_after_confirmation(decision, "unavailable"), ""

        if self.confirmations.cached_decline(action, value):
            self.gate.log_confirmation(
                "confirmation_cached", action=action, value=value,
                status="declined",
            )
            return self.gate.refuse_after_confirmation(decision, "declined"), ""

        if self.confirmations.cap_reached():
            self.gate.log_confirmation(
                "confirmation_skipped", action=action, value=value,
                status="cap_reached", refusals=self.confirmations.refusals,
                cap=self.confirmations.cap,
            )
            return self.gate.refuse_after_confirmation(decision, "cap_reached"), ""

        elicitation_id = new_elicitation_id()
        line = provenance_line(decision)
        request = build_request(action, value, line)
        # Written before the request is sent, so an elicitation that never
        # comes back is still on the record. Exactly one of these per ask.
        self.gate.log_confirmation(
            "elicit_request", elicitation_id=elicitation_id, action=action,
            value=value, client=elicitor.client_name, **request,
        )
        status, choice, raw = await send_elicitation(
            elicitor, request["message"]
        )
        self.gate.log_confirmation(
            "elicit_result", elicitation_id=elicitation_id, action=action,
            value=value, status=status, choice=choice, raw=raw,
        )

        if status != "accepted":
            self.confirmations.record_refusal(action, value, status)
            return (
                self.gate.refuse_after_confirmation(
                    decision, status, elicitation_id=elicitation_id
                ),
                elicitation_id,
            )

        metadata = {
            "elicitation_id": elicitation_id,
            "client": elicitor.client_name,
            "received_at": str(time.time()),
        }
        if choice == "trust_origin_session":
            self.gate.trust_origin_for_session(origin_of(value), metadata)
        self.gate.record_user_confirmed(value, metadata)
        return None, elicitation_id

    # -- page-content provenance ------------------------------------------

    def _record_elements(self, origin: str, elements: list[Element]) -> None:
        blob = "\n".join(
            " ".join(part for part in (e.role, e.name, e.text, e.href) if part)
            for e in elements
        )
        if blob:
            self.gate.record_page_content(origin, blob)

    def _record_blocks(self, origin: str, blocks: list[TextBlock]) -> None:
        blob = "\n".join(b.text for b in blocks)
        if blob:
            self.gate.record_page_content(origin, blob)

    # -- tools -------------------------------------------------------------

    async def navigate(
        self,
        url: str | None = None,
        link_id: str | None = None,
        elicitor: Elicitor | None = None,
    ) -> NavigateResult:
        """Navigate by URL (gated on channel) or by link id (gated on freshness).

        Exactly one of ``url`` or ``link_id``.
        """
        self._intercepted.clear()
        if (url is None) == (link_id is None):
            decision = Decision(
                action="navigate",
                tool="browser.navigate",
                allowed=False,
                decision="deny",
                channel=Channel.UNCLASSIFIED,
                reason="invalid_arguments",
                detail="Provide exactly one of url or link_id.",
                decided_by="server:arguments",
            )
            self.gate.log.write("decision", **decision.as_log_fields())
            return NavigateResult(
                ok=False,
                gate=_as_model(decision),
                origin=self.browser.origin,
                url=self.browser.url,
            )

        if url is not None:
            decision = self.gate.authorize_navigate_url(url)
            if needs_confirmation(decision, self.config.confirm_unclassified):
                refusal, elicitation_id = await self._confirm(
                    decision, elicitor, action="navigate", value=url
                )
                if refusal is not None:
                    return NavigateResult(
                        ok=False,
                        gate=_as_model(refusal),
                        origin=self.browser.origin,
                        url=self.browser.url,
                        title=await self._safe_title(),
                    )
                # The human confirmed. Their answer is provenance now, not a
                # verdict: the gate decides again, with it recorded.
                decision = self.gate.authorize_navigate_url(
                    url, confirmed=True, elicitation_id=elicitation_id
                )
            target = url
        else:
            href = self.browser.resolve_link(link_id or "")
            decision = self.gate.authorize_navigate_link(
                link_id or "",
                href,
                fresh=href is not None,
                page_origin=self.browser.origin,
            )
            target = href or ""

        if not decision.allowed:
            return NavigateResult(
                ok=False,
                gate=_as_model(decision),
                origin=self.browser.origin,
                url=self.browser.url,
                title=await self._safe_title(),
            )

        await self.browser.goto(target)
        error = self.browser.last_navigation_error
        return NavigateResult(
            ok=not error,
            gate=_as_model(decision),
            origin=self.browser.origin,
            url=self.browser.url,
            title=await self._safe_title(),
            error=error,
            blocked=[_as_model(d) for d in self._intercepted if not d.allowed],
        )

    async def snapshot(self) -> SnapshotResult:
        """Structured element inventory.  Records what it returns as PAGE."""
        raw = await self.browser.snapshot()
        elements = [
            Element(id=e.id, role=e.role, name=e.name, text=e.text, href=e.href)
            for e in raw
        ]
        origin = self.browser.origin
        self._record_elements(origin, elements)
        return SnapshotResult(
            origin=origin,
            url=self.browser.url,
            title=await self._safe_title(),
            elements=elements,
        )

    async def read_text(self) -> ReadTextResult:
        """Page text as identified blocks.  Records what it returns as PAGE."""
        raw = await self.browser.read_text()
        blocks = [TextBlock(id=b.id, text=b.text) for b in raw]
        origin = self.browser.origin
        self._record_blocks(origin, blocks)
        return ReadTextResult(origin=origin, blocks=blocks)

    async def click(self, element_id: str) -> ClickResult:
        """Click an element.  Ungated in v0; the navigation it causes is not."""
        self._intercepted.clear()
        error = ""
        try:
            await self.browser.click(element_id)
        except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
            error = f"{type(exc).__name__}: {exc}"
        blocked = [_as_model(d) for d in self._intercepted if not d.allowed]
        return ClickResult(
            ok=not error,
            element_id=element_id,
            origin=self.browser.origin,
            url=self.browser.url,
            title=await self._safe_title(),
            blocked=blocked,
            error=error,
        )

    async def type(
        self, element_id: str, value: str, elicitor: Elicitor | None = None
    ) -> TypeResult:
        """Type into an element.  The value must trace to the USER channel."""
        decision = self.gate.authorize_type(element_id, value)
        if needs_confirmation(decision, self.config.confirm_unclassified):
            refusal, elicitation_id = await self._confirm(
                decision, elicitor, action="type", value=value
            )
            if refusal is not None:
                return TypeResult(
                    ok=False,
                    element_id=element_id,
                    gate=_as_model(refusal),
                    origin=self.browser.origin,
                    url=self.browser.url,
                )
            decision = self.gate.authorize_type(
                element_id, value, confirmed=True, elicitation_id=elicitation_id
            )
        if not decision.allowed:
            return TypeResult(
                ok=False,
                element_id=element_id,
                gate=_as_model(decision),
                origin=self.browser.origin,
                url=self.browser.url,
            )
        error = ""
        try:
            await self.browser.fill(element_id, value)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        return TypeResult(
            ok=not error,
            element_id=element_id,
            gate=_as_model(decision),
            origin=self.browser.origin,
            url=self.browser.url,
            error=error,
        )

    async def back(self) -> BackResult:
        ok = await self.browser.back()
        return BackResult(
            ok=ok,
            origin=self.browser.origin,
            url=self.browser.url,
            title=await self._safe_title(),
        )

    async def _safe_title(self) -> str:
        try:
            return await self.browser.title()
        except Exception:  # noqa: BLE001 -- a title is never worth failing a call
            return ""
