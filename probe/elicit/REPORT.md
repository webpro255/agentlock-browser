# Elicitation probe: SDK surface and what clients do with it

HEAD at time of probe: `32b92f2` (Publish 0.1.0: README install points at PyPI, repository URLs)
mcp version: `2.0.0` (`pip show mcp`: `Name: mcp` / `Version: 2.0.0`)
Python: 3.13.12, from `.venv`

Raw scripted-client output: `probe/elicit/raw_client.txt`. Toy server and
scripted client: `scratchpad/elicit/` (gitignored). Nothing under
`agentlock_browser/` was imported or changed.

Nothing was predicted before this ran.

## 1. What the SDK exposes

### Server side, from inside a tool handler

`mcp/server/mcpserver/context.py:185-215`, the method a tool reaches through
its `Context`:

```python
    async def elicit(
        self,
        message: str,
        schema: type[ElicitSchemaModelT],
    ) -> ElicitationResult[ElicitSchemaModelT]:
        """Elicit information from the client/user.

        This method can be used to interactively ask for additional information from the
        client within a tool's execution. The client might display the message to the
        user and collect a response according to the provided schema. If the client
        is an agent, it might decide how to handle the elicitation -- either by asking
        the user or automatically generating a response.
```

It delegates to `mcp/server/elicitation.py:102-142`:

```python
async def elicit_with_validation(
    session: ServerSession,
    message: str,
    schema: type[ElicitSchemaModelT],
    related_request_id: RequestId | None = None,
) -> ElicitationResult[ElicitSchemaModelT]:
    """Elicit information from the client/user with schema validation (form mode).
```

which calls `mcp/server/session.py:351-380`:

```python
    async def elicit_form(
        self,
        message: str,
        requested_schema: types.ElicitRequestedSchema,
        related_request_id: types.RequestId | None = None,
    ) -> types.ElicitResult:
```

A second mode exists, `elicit_url` (`mcp/server/elicitation.py:145-190`,
`mcp/server/session.py:382-415`), for out-of-band interactions:

```python
async def elicit_url(
    session: ServerSession,
    message: str,
    url: str,
    elicitation_id: str,
    related_request_id: RequestId | None = None,
) -> UrlElicitationResult:
    """Elicit information from the user via out-of-band URL navigation (URL mode).
```

The three result types, `mcp/server/elicitation.py:21-44`:

```python
class AcceptedElicitation(BaseModel, Generic[ElicitSchemaModelT]):
    """Result when user accepts the elicitation."""

    action: Literal["accept"] = "accept"
    data: ElicitSchemaModelT


class DeclinedElicitation(BaseModel):
    """Result when user declines the elicitation."""

    action: Literal["decline"] = "decline"


class CancelledElicitation(BaseModel):
    """Result when user cancels the elicitation."""

    action: Literal["cancel"] = "cancel"


ElicitationResult = TypeAliasType(
    "ElicitationResult",
    AcceptedElicitation[ElicitSchemaModelT] | DeclinedElicitation | CancelledElicitation,
    type_params=(ElicitSchemaModelT,),
)
```

### The capability flag, and where the server reads it

Declared by the client in `ClientCapabilities`, `mcp_types/_types.py:403-404`:

```python
    elicitation: ElicitationCapability | None = None
    """Present if the client supports elicitation from the user."""
```

`mcp_types/_types.py:310-328`:

```python
class FormElicitationCapability(MCPModel):
    """Capability for form mode elicitation."""


class UrlElicitationCapability(MCPModel):
    """Capability for URL mode elicitation (2025-11-25+)."""


class ElicitationCapability(MCPModel):
    """Capability for elicitation operations.

    Clients must support at least one mode (form or url).
    """

    form: FormElicitationCapability | None = None
    """Present if the client supports form mode elicitation."""

    url: UrlElicitationCapability | None = None
    """Present if the client supports URL mode elicitation (2025-11-25 and later)."""
```

The server reads it two ways. `mcp/server/session.py:69-77`:

```python
    @property
    def client_capabilities(self) -> types.ClientCapabilities | None:
        """The capabilities the client declared; `None` when none were declared.

        Prefer this over `client_params.capabilities`: on 2026-07-28+ the
        request envelope declares capabilities while client info stays
        optional, so capabilities can be present without `client_params`.
        """
        return self._connection.client_capabilities
```

and `mcp/server/session.py:137-139`:

```python
    def check_client_capability(self, capability: types.ClientCapabilities) -> bool:
        """Check if the client supports a specific capability."""
        return self._connection.check_capability(capability)
```

whose elicitation branch is `mcp/server/connection.py:453-476`:

```python
    def check_capability(self, capability: ClientCapabilities) -> bool:
        """Return whether the connected client declared the given capability.

        Returns `False` when no capabilities have been recorded.
        """
        ...
        if capability.elicitation is not None and have.elicitation is None:
            return False
```

Note that this branch checks only that `elicitation` is present. It does not
compare `form` against `form` or `url` against `url`, so a client advertising
url mode alone passes a `check_client_capability(ElicitationCapability())`
test.

On the client side the flag is derived, not passed:
`mcp/client/session.py:596-600`:

```python
        elicitation = (
            types.ElicitationCapability(form=types.FormElicitationCapability(), url=types.UrlElicitationCapability())
            if self._elicitation_callback is not _default_elicitation_callback
            else None
        )
```

so a `ClientSession` advertises elicitation exactly when an
`elicitation_callback` was supplied, and it always advertises both modes.
The default, `mcp/client/session.py:226-232`:

```python
async def _default_elicitation_callback(
    context: ClientRequestContext,
    params: types.ElicitRequestParams,
) -> types.ElicitResult | types.ErrorData:
    return types.ErrorData(
        code=types.INVALID_REQUEST,
        message="Elicitation not supported",
    )
```

### The wire schema

Request, `mcp_types/_types.py:1947-2010`:

```python
ElicitRequestedSchema: TypeAlias = dict[str, Any]


class ElicitRequestFormParams(RequestParams):
    """Parameters for form mode elicitation requests.

    Form mode collects non-sensitive information from the user via an in-band form
    rendered by the client.
    """

    mode: Literal["form"] = "form"
    """The elicitation mode (always "form" for this type)."""

    message: str
    """The message to present to the user describing what information is being requested."""

    requested_schema: ElicitRequestedSchema
    """
    A restricted subset of JSON Schema defining the structure of the expected response.
    Only top-level properties are allowed, without nesting.
    """
```

```python
class ElicitRequest(Request[ElicitRequestParams, Literal["elicitation/create"]]):
    """A request from the server to elicit additional information from the user via the client."""

    method: Literal["elicitation/create"] = "elicitation/create"
    params: ElicitRequestParams
```

Result, `mcp_types/_types.py:2012-2030`:

```python
class ElicitResult(Result):
    """The client's response to an elicitation request."""

    action: Literal["accept", "decline", "cancel"]
    """
    The user action in response to the elicitation.
    - "accept": User submitted the form/confirmed the action (or consented to URL navigation)
    - "decline": User explicitly declined the action
    - "cancel": User dismissed without making an explicit choice
    """

    content: dict[str, str | int | float | bool | list[str] | None] | None = None
    """
    The submitted form data, only present when action is "accept" in form mode.
    Contains values matching the requested schema. Values can be strings, integers, floats,
    booleans, arrays of strings, or null.
    For URL mode, this field is omitted.
    """
```

What the server may ask for is constrained. `mcp/server/elicitation.py:74-99`
validates every rendered property against the spec's
`PrimitiveSchemaDefinition` and raises `TypeError` on anything else:

```python
def _validate_rendered_properties(json_schema: dict[str, Any]) -> None:
    """Reject any `properties` entry the spec's `PrimitiveSchemaDefinition` won't accept.

    Catches whatever the renderer let through that isn't spec-valid: bare
    `list[str]` (no enum), multi-primitive unions, nested models.
    """
```

Nothing was "not found": every item asked for in Step 1 exists in this
version.

## 2. Scripted client, E1 to E4

Toy server: one tool `confirm_action(action, value, origin)`, form with one
enum field `choice: ["allow_once", "trust_origin_session", "deny"]`. Each case
is a fresh server process with its own log. Every row is pasted from
`raw_client.txt`.

The message the server sent, identical in E1, E2 and E3:

```
Confirm navigate
value:  http://evil.test/x
origin: http://fixture.test
```

| case | capability advertised | what the server sent | what the client returned | tool result |
|---|---|---|---|---|
| E1 accept | `{"elicitation": {"form": {}, "url": {}}}` | `mode` `"form"`, the message above, `requestedSchema` with `enum: ["allow_once", "trust_origin_session", "deny"]` | `{"action": "accept", "data": {"choice": "allow_once"}}` | `{"action": "accept", "data": {"choice": "allow_once"}, "raw": {"action": "accept", "data": {"choice": "allow_once"}}}`, `is_error: False` |
| E2 decline | `{"elicitation": {"form": {}, "url": {}}}` | same | `{"action": "decline"}` | `{"action": "decline", "data": null, "raw": {"action": "decline"}}`, `is_error: False` |
| E3 cancel | `{"elicitation": {"form": {}, "url": {}}}` | same | `{"action": "cancel"}` | `{"action": "cancel", "data": null, "raw": {"action": "cancel"}}`, `is_error: False` |
| E4 no callback | `{}` | nothing; no `elicitation/create` was sent | callback never ran | `{"elicitation": "unsupported"}`, `is_error: False` |

The full `requestedSchema` the client received, pasted from E1:

```json
{"description": "The form the human is shown.  One enum field, three options.", "properties": {"choice": {"description": "What to do with this action", "enum": ["allow_once", "trust_origin_session", "deny"], "title": "Choice", "type": "string"}}, "required": ["choice"], "title": "Choice", "type": "object"}
```

The server's `capability_check` log line per case:

```
E1  {"event": "capability_check", "supported": true,  "client_capabilities": {"elicitation": {"form": {}, "url": {}}}}
E2  {"event": "capability_check", "supported": true,  "client_capabilities": {"elicitation": {"form": {}, "url": {}}}}
E3  {"event": "capability_check", "supported": true,  "client_capabilities": {"elicitation": {"form": {}, "url": {}}}}
E4  {"event": "capability_check", "supported": false, "client_capabilities": {}}
```

E4's log has two lines only, `capability_check` and `tool_result`. No
`elicit_request` line, because the tool returned before sending one.

Negotiated protocol version in all four cases: `2025-11-25`.

## 3. Real clients (manual)

Steps: `probe/elicit/MANUAL.md`. Run by the operator, not by the probe.

### Claude Desktop

Not measured, no installation available.

### Claude Code

Two runs against the same toy server, one server process, log
`scratchpad/elicit/log_code.jsonl`.

- What appeared on screen: a prompt showing `Confirm navigate`, the value, the
  origin, a `choice: not set` field, and accept and decline buttons.
- Run 1: the human set `choice` to `allow_once` and accepted. Result
  `{"action": "accept", "data": {"choice": "allow_once"}}`.
- Run 2: the human pressed Escape. Result
  `{"action": "cancel", "data": null}`.

Time from `elicit_request` to `elicit_result`, computed from the timestamps
in the lines below: run 1 **145.82 s**, run 2 **3.67 s**.

`log_code.jsonl`, verbatim:

```
{"ts": 1787401622.557482, "event": "capability_check", "supported": true, "client_capabilities": {"elicitation": {}, "roots": {"listChanged": true}}}
{"ts": 1787401622.5576684, "event": "elicit_request", "message": "Confirm navigate\nvalue:  http://evil.test/x\norigin: http://fixture.test", "requested_schema": {"description": "The form the human is shown.  One enum field, three options.", "properties": {"choice": {"description": "What to do with this action", "enum": ["allow_once", "trust_origin_session", "deny"], "title": "Choice", "type": "string"}}, "required": ["choice"], "title": "Choice", "type": "object"}, "mode": "form"}
{"ts": 1787401768.3810427, "event": "elicit_result", "raw": {"action": "accept", "data": {"choice": "allow_once"}}}
{"ts": 1787401768.381246, "event": "tool_result", "result": {"action": "accept", "data": {"choice": "allow_once"}, "raw": {"action": "accept", "data": {"choice": "allow_once"}}}}
{"ts": 1787401840.0171673, "event": "capability_check", "supported": true, "client_capabilities": {"elicitation": {}, "roots": {"listChanged": true}}}
{"ts": 1787401840.0198264, "event": "elicit_request", "message": "Confirm navigate\nvalue:  http://evil.test/x\norigin: http://fixture.test", "requested_schema": {"description": "The form the human is shown.  One enum field, three options.", "properties": {"choice": {"description": "What to do with this action", "enum": ["allow_once", "trust_origin_session", "deny"], "title": "Choice", "type": "string"}}, "required": ["choice"], "title": "Choice", "type": "object"}, "mode": "form"}
{"ts": 1787401843.686245, "event": "elicit_result", "raw": {"action": "cancel"}}
{"ts": 1787401843.6863801, "event": "tool_result", "result": {"action": "cancel", "data": null, "raw": {"action": "cancel"}}}
```

## Observed, not interpreted

### Does the SDK distinguish decline from cancel

Yes, at both layers, and the distinction survives the round trip.

On the wire, `ElicitResult.action` is
`Literal["accept", "decline", "cancel"]` with the three documented as
"User explicitly declined the action" versus "User dismissed without making an
explicit choice" (`mcp_types/_types.py:2016-2022`). On the server side they
are separate classes, `DeclinedElicitation` and `CancelledElicitation`
(`mcp/server/elicitation.py:28-37`).

E2 and E3 measured that end to end: the same tool, same schema, returned
`{"action": "decline", ...}` and `{"action": "cancel", ...}` respectively.

Neither carries `content`: `content` is documented as present "only when
action is accept in form mode", and E2 and E3 both came back with `data`
null.

### Can a non-advertising client be detected before the tool runs

The capability is readable inside the handler, before any elicitation is
sent. E4 recorded `supported: false` and `client_capabilities: {}` and
returned without sending an `elicitation/create`.

Whether it is readable strictly *before the tool runs*, in the sense of a
tool-listing or registration-time decision, is a different question and this
probe did not measure it. What it measured is that
`ctx.request_context.session.client_capabilities` and
`check_client_capability` both answer inside the handler at the top, before
`ctx.elicit` is called.

Two details in that path, both pasted above rather than inferred:

* `check_capability` tests only that `elicitation` is present, not which mode
  (`mcp/server/connection.py:475-476`), so a url-only client passes a form
  check.
* A `ClientSession` advertises elicitation exactly when an
  `elicitation_callback` was supplied, and then always advertises both form
  and url (`mcp/client/session.py:596-600`). E4 advertised `{}` because no
  callback was passed.

### Escape produced cancel, not decline

Claude Code run 2: the human pressed Escape and the client returned
`{"action": "cancel"}`, not `{"action": "decline"}`. The prompt carried an
explicit decline button, which was not the button pressed. So on this client
the two are reachable by different gestures, and dismissal is the one that
maps to `cancel`.

### The deny option inside the enum produces an accept-shaped result

The toy form put `deny` in the enum alongside `allow_once` and
`trust_origin_session`. Choosing it and submitting is a submission: the client
returns `action` `"accept"` with `data.choice` `"deny"`, which is shaped
exactly like a grant and is distinguishable only by reading the payload. That
path was not exercised in either run, and it follows from the schema rather
than from a measurement. The option is dropped from the design; the client's
own decline is the no.

### The elicitation blocked the tool call for the full human response time

`confirm_action` did not return until the human answered. Run 1 held the tool
call for 145.82 s, run 2 for 3.67 s, measured from `elicit_request` to
`elicit_result` in the log lines above. Nothing in the SDK path timed out or
intervened.

### Claude Code advertised elicitation with no mode sub-flag

Both `capability_check` lines from Claude Code recorded:

```
"client_capabilities": {"elicitation": {}, "roots": {"listChanged": true}}
```

`elicitation` is an empty object. The scripted `ClientSession` in E1 to E3
advertised `{"elicitation": {"form": {}, "url": {}}}` instead. Both clients
rendered form mode, and Claude Code did so with `form` absent from what it
declared. A check written against `client_capabilities.elicitation.form`
would read `None` for Claude Code; the coarse
`check_client_capability(ClientCapabilities(elicitation=ElicitationCapability()))`
returned `supported: true` for it, which is the value pasted in those same
two lines.

### Harness note

The first run of the scripted client raised
`AttributeError: 'ClientSession' object has no attribute '_client_capabilities'`.
That was a wrong attribute name in the probe's own client script, not
behaviour of the SDK: the accessor is `_build_capabilities(version)`
(`mcp/client/session.py:576`). It was corrected and all four cases then ran
without exception. No case required a change under `agentlock_browser/`.
