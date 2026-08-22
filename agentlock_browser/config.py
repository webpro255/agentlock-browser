"""Configuration for the agentlock-browser MCP server.

Everything the operator controls lives here.  Nothing in this module is
reachable by the model: config is loaded once at startup, from a JSON file
and/or the environment, before the first tool call is served.

Copyright 2026 David Grice
SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

__all__ = ["BrowserConfig", "origin_of"]


def origin_of(url: str) -> str:
    """The scheme://host[:port] origin of a URL, lowercased.

    Returns "" for a URL with no scheme or host (about:blank, data:, "").
    """
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


@dataclass
class BrowserConfig:
    """Operator-controlled configuration.

    Attributes:
        allowlist: Origins the operator has pre-authorized for navigation.
            Values in this list are the ALLOWLIST provenance channel.  Compared
            as origins (scheme://host[:port]), never as substrings.
        operator_text: The operator's own message text for this session.  This
            is the USER provenance channel and the ONLY source of it.  See
            README "The USER channel" for why this is a startup input and not
            a tool argument.
        log_path: JSONL decision log.  Every gate decision and every provenance
            record is appended here.
        headless: Run chromium headless.  Default True.
        chromium_args: Extra chromium launch arguments.  Used by the test
            harness to map evil.test onto a local server.
        user_agent: Optional user-agent override.
        nav_timeout_ms: Navigation timeout.
        action_timeout_ms: Click/type timeout.
        settle_ms: How long to let work a click triggered settle before
            reporting the result, so an intercepted navigation is decided
            before the caller reads the outcome.
        role: The role presented to the AgentLock gate.
        signing_key: HMAC-SHA256 receipt signing key.  Generated per process
            when absent, which makes receipts verifiable only in-process.
    """

    allowlist: list[str] = field(default_factory=list)
    operator_text: str = ""
    log_path: str = "agentlock-browser.jsonl"
    headless: bool = True
    chromium_args: list[str] = field(default_factory=list)
    user_agent: str | None = None
    nav_timeout_ms: int = 30_000
    action_timeout_ms: int = 10_000
    settle_ms: int = 500
    role: str = "operator"
    signing_key: bytes | None = None

    def __post_init__(self) -> None:
        # Normalize the allowlist to origins once, at load, so no comparison
        # downstream ever has to guess whether it holds a URL or an origin.
        self.allowlist = [origin_of(a) or a.strip().lower() for a in self.allowlist]

    def is_allowlisted(self, url: str) -> bool:
        """Is this URL's origin on the operator's allowlist?

        Origin equality, not prefix or substring: "https://evil.com/example.com"
        does not match an allowlisted "https://example.com".
        """
        origin = origin_of(url)
        return bool(origin) and origin in self.allowlist

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> BrowserConfig:
        """Load config from a JSON file and the environment.

        Environment overrides the file.  Recognized variables:

        * ``AGENTLOCK_BROWSER_CONFIG``   -- path to the JSON file
        * ``AGENTLOCK_BROWSER_ALLOWLIST`` -- comma-separated origins
        * ``AGENTLOCK_BROWSER_OPERATOR_TEXT`` -- the operator's message text
        * ``AGENTLOCK_BROWSER_LOG``      -- JSONL decision log path
        * ``AGENTLOCK_BROWSER_HEADLESS`` -- "0"/"false" to run headed
        """
        data: dict[str, object] = {}
        cfg_path = path or os.environ.get("AGENTLOCK_BROWSER_CONFIG")
        if cfg_path:
            p = Path(cfg_path).expanduser()
            if p.exists():
                data = json.loads(p.read_text())

        allowlist_env = os.environ.get("AGENTLOCK_BROWSER_ALLOWLIST")
        if allowlist_env is not None:
            data["allowlist"] = [a for a in allowlist_env.split(",") if a.strip()]

        operator_env = os.environ.get("AGENTLOCK_BROWSER_OPERATOR_TEXT")
        if operator_env is not None:
            data["operator_text"] = operator_env

        log_env = os.environ.get("AGENTLOCK_BROWSER_LOG")
        if log_env:
            data["log_path"] = log_env

        headless_env = os.environ.get("AGENTLOCK_BROWSER_HEADLESS")
        if headless_env is not None:
            data["headless"] = headless_env.strip().lower() not in ("0", "false", "no")

        known = {f for f in cls.__dataclass_fields__ if f != "signing_key"}
        return cls(**{k: v for k, v in data.items() if k in known})  # type: ignore[arg-type]
