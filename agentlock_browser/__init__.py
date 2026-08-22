"""agentlock-browser -- provenance-gated browsing tools for AI agents.

Adversarial and legitimate tool requests are semantically identical.  What
distinguishes them is not what the request says but where its values came
from.  This server gates browsing on that, at the infrastructure layer, using
AgentLock.

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

__version__ = "0.2.0"

from agentlock_browser.config import BrowserConfig
from agentlock_browser.gate import BrowserGate, Decision
from agentlock_browser.provenance import Channel, ProvenanceLedger
from agentlock_browser.service import BrowserService

__all__ = [
    "BrowserConfig",
    "BrowserGate",
    "BrowserService",
    "Channel",
    "Decision",
    "ProvenanceLedger",
    "__version__",
]
