"""Provenance channels and the ledger that records them into AgentLock.

Five channels.  Four were frozen in PREDICTIONS.md before any code existed;
``USER_CONFIRMED`` was added in 0.2.0 under amendment 2026-08-23b:

* ``USER``      -- text from the operator's message
* ``USER_CONFIRMED`` -- a value the operator confirmed when the server asked
                   them, through an MCP elicitation
* ``ALLOWLIST`` -- operator-configured origins, loaded at startup
* ``PAGE``      -- any value originating from page content, tagged with the
                   page origin it was read from
* ``MODEL``     -- values composed by the model with no page or user lineage

AgentLock tags *context entries*, not values: an entry is written with a
``ContextSource`` whose ``ContextAuthority`` is AUTHORITATIVE / DERIVED /
UNTRUSTED, and the provenance of a *parameter value* is then derived by the
gate's lineage engine.  That model has three authority levels where this
server needs four channels, so the ledger below keeps the channel label and
the page origin alongside each entry and recovers them by ``provenance_id``
from the evidence the gate cites.  See NEEDS.md for what that costs.

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from agentlock import AuthorizationGate, ContextSource

__all__ = ["Channel", "LedgerEntry", "ProvenanceLedger"]


class Channel(str, Enum):
    """The provenance channel a value traces to."""

    USER = "USER"
    #: The operator confirmed this exact value out of band, through an MCP
    #: elicitation the client showed them.  Authoritative like USER, and kept
    #: distinct so an auditor can tell a value the operator typed from one
    #: they were asked about.
    USER_CONFIRMED = "USER_CONFIRMED"
    ALLOWLIST = "ALLOWLIST"
    PAGE = "PAGE"
    MODEL = "MODEL"
    #: The gate allowed the call but no channel could be established for the
    #: value.  Not one of the four channels -- a hole, surfaced rather than
    #: papered over.  See NEEDS.md item 3.
    UNCLASSIFIED = "UNCLASSIFIED"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class LedgerEntry:
    """One recorded context entry, with the channel AgentLock does not keep."""

    provenance_id: str
    channel: Channel
    origin: str
    content: str
    session_id: str


class ProvenanceLedger:
    """Records provenance into the AgentLock gate and remembers the channel.

    Every write goes through :meth:`AuthorizationGate.notify_context_write`,
    so the gate's lineage engine sees exactly what this server saw.  The
    ledger keeps a parallel index only for what AgentLock does not model: the
    channel label and, for PAGE, the origin the content was read from.
    """

    def __init__(self, gate: AuthorizationGate) -> None:
        self._gate = gate
        self._entries: dict[str, LedgerEntry] = {}

    @staticmethod
    def hash_content(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    def _record(
        self,
        session_ids: list[str],
        source: ContextSource,
        content: str,
        channel: Channel,
        origin: str = "",
        extra_metadata: dict[str, str] | None = None,
    ) -> list[LedgerEntry]:
        entries: list[LedgerEntry] = []
        metadata = {"channel": channel.value, "origin": origin}
        metadata.update(extra_metadata or {})
        for sid in session_ids:
            prov = self._gate.notify_context_write(
                sid,
                source,
                self.hash_content(content),
                writer_id=origin or channel.value,
                content=content,
                metadata=metadata,
            )
            entry = LedgerEntry(
                provenance_id=prov.provenance_id,
                channel=channel,
                origin=origin,
                content=content,
                session_id=sid,
            )
            self._entries[prov.provenance_id] = entry
            entries.append(entry)
        return entries

    def record_user_text(self, session_ids: list[str], text: str) -> list[LedgerEntry]:
        """Record the operator's own message.  Channel USER, AUTHORITATIVE."""
        return self._record(session_ids, ContextSource.USER_MESSAGE, text, Channel.USER)

    def record_user_confirmed(
        self, session_ids: list[str], value: str, metadata: dict[str, str]
    ) -> list[LedgerEntry]:
        """Record a value the operator confirmed out of band.

        Written as ``USER_MESSAGE`` because that is what it is: the operator
        said this value, just through the client's confirmation prompt rather
        than through their opening instruction.  The channel label keeps the
        two apart in the log.
        """
        return self._record(
            session_ids,
            ContextSource.USER_MESSAGE,
            value,
            Channel.USER_CONFIRMED,
            extra_metadata=metadata,
        )

    def record_allowlist(self, session_ids: list[str], value: str) -> list[LedgerEntry]:
        """Record trusted operator configuration.  Channel ALLOWLIST.

        Written as ``SYSTEM_PROMPT`` because that is AgentLock's authoritative
        non-user source.  Used both for the allowlist loaded at startup and
        for a concrete URL whose origin the operator allowlisted.
        """
        return self._record(
            session_ids, ContextSource.SYSTEM_PROMPT, value, Channel.ALLOWLIST
        )

    def record_page(
        self, session_ids: list[str], origin: str, text: str
    ) -> list[LedgerEntry]:
        """Record content read from a page.  Channel PAGE(origin), UNTRUSTED."""
        return self._record(
            session_ids, ContextSource.WEB_CONTENT, text, Channel.PAGE, origin=origin
        )

    def get(self, provenance_id: str) -> LedgerEntry | None:
        return self._entries.get(provenance_id)

    def user_text(self) -> str:
        """Everything recorded on the USER channel, joined."""
        seen: list[str] = []
        for e in self._entries.values():
            if e.channel is Channel.USER and e.content not in seen:
                seen.append(e.content)
        return "\n".join(seen)
