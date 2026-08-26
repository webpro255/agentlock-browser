# Changelog

This file starts at 0.2.0. For 0.1.0, read the git history.

## 0.2.0

Human confirmation, over MCP elicitation. The USER channel is a startup string,
so a URL the model composed or read off a page can never trace to the operator
however reasonable it is. Elicitation is the one point in the protocol where a
server can reach the person mid-call, and that is where the gap is closed.

Four actions now ask:

- `navigate(url)` denied on MODEL or PAGE, or allowed on a value the gate could
  not classify.
- `type(value)` on the same terms.
- `navigate(link_id)` whose href leaves both the current page origin and the
  session allowlist. Freshness says the id came from the page the model is
  looking at; it says nothing about where the href points.
- A cross-origin navigation the page itself caused (a click, a redirect hop, a
  meta refresh, a script) while a tool call is in flight.

Mechanism:

- **The interceptor is unchanged.** A refused navigation is still answered 204
  and logged synchronously, and the page still stays where it was. The
  confirmation runs after that, inside the same tool call. On a yes the target
  is recorded as provenance and the server issues a new gated navigation to it,
  logged with `"cause": "post_confirm"`, which the gate decides.
- **One prompt per hop.** A confirmed navigation that redirects cross-origin
  again is a separate refusal, a separate prompt and a separate gate decision.
  Never one yes to a chain of unknown length. Measured cost on a real site:
  `probe/manual/REPORT.md` run 2, four confirmations to follow one link, and
  `NEEDS.md` item 9.
- **A confirmation is provenance, not a verdict.** An accepted value is
  recorded on a channel of its own, `USER_CONFIRMED`, and the call is
  authorized again from the top. The gate decides both times. Only two
  decisions are the server's, whether to ask and whether an earlier decline
  still stands, and both are logged `decided_by: "server:confirm"`.
- **Two choices**, `allow_once` and `trust_origin_session`. There is no deny
  option in the form: the client's own decline is the no.
- **Decline and cancel are different answers.** A decline is cached per
  (action, value) for the session and the identical action is denied without
  asking again. A cancel is a dismissal, not an answer, and asks again.
- **A cap.** After 5 declines or dismissals in one session the server stops
  asking and denials carry `confirmation: "cap_reached"`. Configurable with
  `confirm_cap`. Whether the unclassified fail-open case asks at all is
  `confirm_unclassified`, default on.
- **Capability rule.** Form elicitation counts as available when the client
  declares `elicitation` and either names `form` or names no mode at all;
  url-only cannot show a form and gets `confirmation: "unavailable"`. The
  modes-unspecified case is not a guess: Claude Code advertises
  `{"elicitation": {}}` while rendering a form to a person, where the SDK's own
  client advertises both modes.
- **A navigation with no tool call in flight is not confirmed.** A meta refresh
  on a timer, or a script that navigates after the call returned, denies and
  logs as before with no prompt. Named limitation.
- **Clients without form elicitation reduce to 0.1.0 behaviour exactly**, for
  every case above: no prompt, no extra log line, and the result 0.1.0 would
  have returned.

Also:

- The gate block on every tool result carries `fail_open`, the same value the
  log line carries. A fail-open grant is now returned to the caller in the
  words the log uses, not only recorded.
- A navigation the page caused names its `target` on the result.
- Every elicitation request and result is written to the JSONL log verbatim.

Measured on Claude Code and on no other client. Claude Desktop, Cursor, VS Code
and everything else are unmeasured, and nothing here claims what they do.

Regressions: `tests/test_confirmation.py` (EL1 to EL10, EL8b, EL8c) and
`tests/test_confirmation_nav.py` (EM1 to EM10), both driven through a real MCP
client over stdio with a scripted callback tagged `SCRIPTED_HUMAN` standing in
for the human. Expectations are frozen in `PREDICTIONS.md` amendments
2026-08-23b through 2026-08-23e.
