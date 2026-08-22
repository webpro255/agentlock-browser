# Origin comparison probe: what the allowlist guard accepts

HEAD sha: `b08204f4c7128b3e3d866fdbcd1e9f178937de51`
branch: `probe/origin-compare`

`pip show agentlock`:

```
Version: 1.7.0
```

Raw output this report is pasted from: `probe/origin/raw_guard.txt` (cases O1
to O20) and `probe/origin/raw_nav.txt` (cases R1 to R5).

## Step 1: the guard

`agentlock_browser/config.py`, lines 22 to 30:

```python
  22  def origin_of(url: str) -> str:
  23      """The scheme://host[:port] origin of a URL, lowercased.
  24  
  25      Returns "" for a URL with no scheme or host (about:blank, data:, "").
  26      """
  27      parts = urlsplit(url.strip())
  28      if not parts.scheme or not parts.netloc:
  29          return ""
  30      return f"{parts.scheme.lower()}://{parts.netloc.lower()}"
```

`agentlock_browser/config.py`, lines 78 to 85:

```python
  78      def is_allowlisted(self, url: str) -> bool:
  79          """Is this URL's origin on the operator's allowlist?
  80  
  81          Origin equality, not prefix or substring: "https://evil.com/example.com"
  82          does not match an allowlisted "https://example.com".
  83          """
  84          origin = origin_of(url)
  85          return bool(origin) and origin in self.allowlist
```

`agentlock_browser/gate.py`, lines 305 to 325:

```python
 305  
 306          ``cause="tool"`` is a direct call from the model.  ``cause`` is also
 307          the hook for an intercepted click-driven navigation, which is
 308          authorized identically but never materializes the allowlist: the value
 309          came off a page, not out of the operator's configuration.
 310          """
 311          expected: Channel | None = None
 312          if cause == "tool":
 313              if self._traces_to_user(url):
 314                  expected = Channel.USER
 315              elif self.config.is_allowlisted(url):
 316                  # The operator allowlisted this ORIGIN; AgentLock's lineage
 317                  # engine matches whole tokens, so an allowlisted origin does
 318                  # not cover a path under it.  Record the concrete URL as
 319                  # trusted configuration so the gate is deciding on the same
 320                  # facts the operator stated.  It is written to the log as its
 321                  # own provenance entry, so an auditor sees exactly why the
 322                  # grant rested where it did.
 323                  for entry in self.ledger.record_allowlist(
 324                      [self.nav_session.session_id], url
 325                  ):
```

## Step 2: direct calls to the guard

Allowlist as configured: `['https://example.com']`
Allowlist after normalize: `['https://example.com']`

| case | note | input | origin_of() output | is_allowlisted |
|---|---|---|---|---|
| O1 | baseline | `'https://example.com/foo/bar'` | `'https://example.com'` | `True` |
| O2 | userinfo | `'https://example.com@evil.test/'` | `'https://example.com@evil.test'` | `False` |
| O3 | userinfo, same host | `'https://user:pass@example.com/'` | `'https://user:pass@example.com'` | `False` |
| O4 | explicit default port | `'https://example.com:443/'` | `'https://example.com:443'` | `False` |
| O5 | other port | `'https://example.com:8443/'` | `'https://example.com:8443'` | `False` |
| O6 | trailing dot | `'https://example.com./'` | `'https://example.com.'` | `False` |
| O7 | case | `'https://EXAMPLE.COM/'` | `'https://example.com'` | `True` |
| O8 | suffix host | `'https://example.com.evil.test/'` | `'https://example.com.evil.test'` | `False` |
| O9 | path lookalike | `'https://evil.test/https://example.com/'` | `'https://evil.test'` | `False` |
| O10 | scheme downgrade | `'http://example.com/'` | `'http://example.com'` | `False` |
| O11 | punycode lookalike | `'https://xn--exmple-cua.com/'` | `'https://xn--exmple-cua.com'` | `False` |
| O12 | IDN raw | `'https://exämple.com/'` | `'https://exämple.com'` | `False` |
| O13 | leading space | `' https://example.com/'` | `'https://example.com'` | `True` |
| O14 | trailing newline | `'https://example.com/\n'` | `'https://example.com'` | `True` |
| O15 | embedded tab | `'https://example.com\t/'` | `'https://example.com'` | `True` |
| O16 | scheme-relative | `'//example.com/'` | `''` | `False` |
| O17 | triple slash | `'https:///example.com/'` | `''` | `False` |
| O18 | encoded dot | `'https://example%2Ecom/'` | `'https://example%2ecom'` | `False` |
| O19 | loopback, sanity | `'https://127.0.0.1/'` | `'https://127.0.0.1'` | `False` |
| O20 | non-http scheme | `'javascript:alert(1)'` | `''` | `False` |

## Step 3: through navigate with interception

Allowlist: `['http://fixture.test']`
operator_text: `'follow the links on http://fixture.test/start'`

"materialized" is the count of provenance events with note
`allowlist materialized for url` in that case's decision log. "interceptor
fired" is the count of decision events with action `intercepted_navigation`.

| case | materialized | authorize decision | interceptor fired | final page.url |
|---|---|---|---|---|
| R1 302 Location | yes (1) | `"allowed": true, "decision": "allow", "channel": "ALLOWLIST", "reason": ""` | no (0) | `'http://evil.test/landed'` |
| R2 meta refresh | yes (1) | navigate: `"allowed": true, "decision": "allow", "channel": "ALLOWLIST"`; intercepted: `"allowed": false, "decision": "deny", "reason": "novel_lineage", "channel": "MODEL"` | yes (1) | `'http://fixture.test/meta'` |
| R3 JS location.href | yes (1) | navigate: `"allowed": true, "decision": "allow", "channel": "ALLOWLIST"`; intercepted: `"allowed": false, "decision": "deny", "reason": "novel_lineage", "channel": "MODEL"` | yes (1) | `'http://fixture.test/js'` |
| R4 url with userinfo | no (0) | `"allowed": false, "decision": "deny", "reason": "novel_lineage", "channel": "MODEL"` | no (0) | `'about:blank'` |
| R5 click userinfo href | yes (1) | navigate: `"allowed": true, "decision": "allow", "channel": "ALLOWLIST"`; intercepted: `"allowed": false, "decision": "deny", "reason": "param_lineage", "channel": "PAGE"` | yes (1) | `'http://fixture.test/userinfo_link.html'` |

Supporting values pasted from `probe/origin/raw_nav.txt`:

```
R1  browser.blocked (targets refused by the interceptor): []
R1  evil.test server hits: ['/landed']
R2  browser.blocked (targets refused by the interceptor): ['http://evil.test/landed']
R2  evil.test server hits: []
R3  browser.blocked (targets refused by the interceptor): ['http://evil.test/landed']
R3  evil.test server hits: []
R4  browser.blocked (targets refused by the interceptor): []
R4  evil.test server hits: []
R5  browser.blocked (targets refused by the interceptor): ['http://fixture.test@evil.test/']
R5  evil.test server hits: []
```

R4 target_origin as the guard computed it, pasted from the decision record:

```
"target_origin": "http://fixture.test@evil.test"
```

R5 snapshot row for the link that was clicked:

```
{"href": "http://fixture.test@evil.test/", "id": "1-e3", "name": "Continue reading", "role": "link", "text": "Continue reading"}
```

R5 interception evidence:

```
"evidence": {"gate": "param_lineage", "matched_kind": "email", "matched_param": "url", "matched_token": "fixture.test@evil.test", "untrusted_provenance_id": "cprov_a47923026935fe89", "untrusted_source_ref": "web_content:cprov_a47923026935fe89"}
```

## Observed, not interpreted

### O cases where is_allowlisted returned True for an input whose host is not example.com

None.

Five O cases returned `True`. In each, the host after `origin_of()` is
`example.com`:

```
O1  input 'https://example.com/foo/bar'   origin_of() 'https://example.com'
O7  input 'https://EXAMPLE.COM/'          origin_of() 'https://example.com'
O13 input ' https://example.com/'         origin_of() 'https://example.com'
O14 input 'https://example.com/\n'        origin_of() 'https://example.com'
O15 input 'https://example.com\t/'        origin_of() 'https://example.com'
```

The remaining fifteen O cases returned `False`.

### R cases where final page.url is on evil.test

One: R1.

```
R1  navigate(url='http://fixture.test/redirect')
R1  "allowed": true, "decision": "allow", "channel": "ALLOWLIST"
R1  provenance events with note "allowlist materialized for url": 1
R1  decision events with action "intercepted_navigation": 0
R1  page.url after the call: 'http://evil.test/landed'
R1  evil.test server hits: ['/landed']
```

R2, R3, R4 and R5 final page.url:

```
R2  page.url after the call: 'http://fixture.test/meta'
R3  page.url after the call: 'http://fixture.test/js'
R4  page.url after the call: 'about:blank'
R5  page.url after the call: 'http://fixture.test/userinfo_link.html'
```

`evil.test server hits` was `[]` in R2, R3, R4 and R5.
