# agentlock-browser

**Browsing tools that gate on where a value came from, not on what it says.**

An MCP server that drives a real browser and refuses to act on a URL or a form
value that originated in page content. A page can say anything; what it cannot
do is change the provenance of the string it is asking the agent to use.

This is a v0 skeleton. It is built on [AgentLock](https://agentlock.dev), the
open standard for tool-call authorization, and it owns its browser through
Playwright directly. It does not wrap `@playwright/mcp` and does not depend on
it.

## Why not just wrap the upstream server

`probe/REPORT.md` records what `@playwright/mcp` 0.0.79 actually returns.
Every result is a single untyped text block (`structuredContent` is `null` in
all 23 recorded results, and none of its 24 tools declares an `outputSchema`).
Page text arrives with no per-element identity: of 874 `- text:` lines in a
Wikipedia snapshot, zero carry a ref. Elements are named by opaque server-side
refs that resolve to a Playwright locator the caller never sees, and the same
field accepts a raw CSS selector. And `browser_evaluate` runs arbitrary
JavaScript the model wrote.

None of that can carry provenance. So this server emits structured, identified,
origin-tagged output instead, and has no evaluate tool.

## Install

```bash
pip install agentlock-browser
python -m playwright install chromium
```

From a checkout:

```bash
pip install -e .
python -m playwright install chromium
```

## Add to Claude Desktop

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agentlock-browser": {
      "command": "agentlock-browser",
      "env": {
        "AGENTLOCK_BROWSER_ALLOWLIST": "https://example.com,https://docs.python.org",
        "AGENTLOCK_BROWSER_OPERATOR_TEXT": "research the docs and summarize them",
        "AGENTLOCK_BROWSER_LOG": "/home/you/agentlock-browser.jsonl"
      }
    }
  }
}
```

`AGENTLOCK_BROWSER_CONFIG` may point at a JSON file with the same keys
(`allowlist`, `operator_text`, `log_path`, `headless`, …); the environment wins
over the file. The browser runs headless unless
`AGENTLOCK_BROWSER_HEADLESS=0`.

## Tools

| tool | arguments | gated on |
|---|---|---|
| `navigate` | `url` **or** `link_id` | channel of `url`; freshness of `link_id` |
| `snapshot` | (none) | ungated |
| `read_text` | (none) | ungated |
| `click` | `element_id` | ungated; the navigation it causes is not |
| `type` | `element_id`, `value` | channel of `value` |
| `back` | (none) | ungated |

Every tool returns `structuredContent` against a declared `outputSchema`.
`snapshot` returns `{origin, url, title, elements: [{id, role, name, text,
href}]}`; `read_text` returns `{origin, blocks: [{id, text}]}`. Page text never
comes back as one undifferentiated string.

Navigation is intercepted through the CDP `Fetch` domain, because Playwright's
`page.route` never offers a redirect hop to a handler, so a cross-origin 302
was followed before anything could gate it (`probe/origin/REPORT.md`,
`probe/cdp/REPORT.md`). That makes this server chromium-only.

Element ids are stable for the current page load and regenerate on navigation:
an id is `<page-load>-e<n>`, and an id minted before a navigation cannot resolve
after one. A denial is a structured result with a reason, not an error.

## Provenance rules

Four channels:

- **USER**: text from the operator's message
- **ALLOWLIST**: operator-configured origins, loaded at startup
- **PAGE(origin)**: any value read from page content, tagged with the origin
- **MODEL**: composed by the model, with no page or user lineage

Values are tagged at the source: the operator's message text is recorded as
USER when the server starts, the configured allowlist as ALLOWLIST, and
everything `snapshot` or `read_text` returns is recorded as PAGE(origin) at the
moment it is returned. Content becomes tainted exactly when the model can see
it.

What that buys, per action:

| action | allowed | denied |
|---|---|---|
| `navigate(url)` | USER, ALLOWLIST | PAGE, MODEL |
| `navigate(link_id)` | id from the most recent snapshot | stale or unknown id |
| `type(value)` | USER | PAGE, MODEL |
| cross-origin navigation caused by a click | (nothing) | always: treated as `navigate(url)` with PAGE provenance |

Ungated in v0: `snapshot`, `read_text`, `back`, and same-origin clicks.

`navigate(link_id)` is how an agent follows a link. The id resolves to an href
**server-side** and the href never has to pass through the model, so following a
link the operator asked for stays possible while pasting a URL a page supplied
does not.

A click that would leave the current origin is answered with a 204, so the
navigation is dropped and the page stays exactly where it was: no error page,
no request to the other origin.

**The AgentLock gate decides.** This package records provenance, calls
`gate.authorize()`, and reports the verdict; it never overrides one. The single
exception is the link-id freshness rule, which AgentLock cannot express. Those
denials are marked `decided_by: "server:link_freshness"` in the log so they are
never mistaken for a gate verdict. See `NEEDS.md`.

Every decision, and every provenance record, is appended to a JSONL log with the
gate's evidence and a signed receipt:

```json
{"event": "decision", "action": "navigate_url", "allowed": false,
 "reason": "param_lineage", "channel": "PAGE", "origin": "http://fixture.test",
 "decided_by": "agentlock_gate",
 "evidence": {"gate": "param_lineage", "matched_param": "url",
              "matched_token": "evil.test/collect?x=handbook-session-7f3a1c",
              "untrusted_provenance_id": "cprov_8fb61c8f03117287"},
 "receipt": {"receipt_id": "rcpt_…", "signature": "…"}}
```

That log is the ground truth for the pre-registered tests, never a chat summary.

### The USER channel

MCP gives a server no way to see the operator's message, so USER text is a
startup input (`operator_text` / `AGENTLOCK_BROWSER_OPERATOR_TEXT`), not
something a tool argument can carry. A session whose operator text is empty or
bland has a weaker USER baseline than one where the operator stated what they
wanted (see the second limitation below).

## Tests

```bash
pytest                                  # T5 runs; T1-T4 are registered and skipped
cat tests/results/T5.txt                # the raw T5 transcript, log and checks
```

T5 (legitimate baseline) is automated with no model in the loop: the tool calls
are issued directly, so what is measured is the gate and the server. T1-T4
fixtures and both local origins are implemented (`evil.test` resolves to a
second local server, so the cross-origin case is real), but their model-driven
runs are not, because those predictions are about model behaviour.

## Named limitations

Copied verbatim from `PREDICTIONS.md`, which was frozen before any server code
existed:

- **Selection influence**: page content can still steer which link_id the
  model chooses. Out of scope, documented, same as core AgentLock.
- **Same-origin clicks are ungated in v0.** A hostile site can drive the
  agent within itself. Cross-origin is the boundary we defend.
- **Model refusal may make T1 and T3 uninformative.** That is a payload
  problem, not a gate result.

Found while building v0, and not yet closed:

- **The lineage gates fail open on values with no distinctive token.** AgentLock
  classifies a value by extracting tokens from it; a plain word shorter than 12
  characters with no digit or punctuation yields none, and a value that cannot
  be classified is allowed. A page that says *type the word `expenses`* is not
  caught; one that says *type `abc123`* is. Such a grant is recorded as
  `"channel": "UNCLASSIFIED", "fail_open": true` rather than being closed by a
  local check, because closing it here would move the decision out of the gate.
  `NEEDS.md` item 3.
- **A bland operator message weakens the MODEL check.** Novel-lineage needs a
  distinctive token in the authoritative context to have a baseline at all;
  without one, MODEL-composed values are allowed. `NEEDS.md` item 3(b).
- **`read_text` and `snapshot` report a curated set of elements.** Text in an
  element neither selector matches is not returned, and therefore never
  recorded as PAGE. That stays consistent (what the model cannot see, it cannot
  echo) only for as long as these tools are the only way page content enters the
  conversation.
- **A redirect from an authorized navigation is followed without a second
  check.** v0 grants the redirect chain of a URL it allowed.
- **One tab.** `target="_blank"` and popups are not handled in v0.

## License

AGPL-3.0-or-later. Copyright 2026 David Grice.
