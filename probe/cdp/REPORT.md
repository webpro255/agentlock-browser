# CDP Fetch probe: redirect hop visibility and failRequest behavior

HEAD at time of probe: `6a3e162` (PREDICTIONS amendment 2026-08-22c)
Chromium: `HeadlessChrome/151.0.7922.34` (from the recorded request headers)

Raw output: `probe/cdp/raw.txt`. Script: `scratchpad/probe_cdp.py` (gitignored).
Nothing under `agentlock_browser/` was imported or changed.

Interception was enabled as:

```
Fetch.enable patterns: [{"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}]
```

Context: `scratchpad/hop_routable.py` recorded that `page.route` and
`context.route` never offer a redirect hop to a handler. This probe asks
whether `Fetch.requestPaused` does.

## Cases

"requestPaused fired for hop" means an event whose `request.url` is the
cross-origin target. "hop carried redirect reference" names the field on that
event that points back at the request it came from, or `none` when the event
carries no such field.

| case | requestPaused fired for hop | hop carried redirect reference | final page.url | evil.test hits | error text |
|---|---|---|---|---|---|
| C1 goto 302, continue all | yes | `redirectedRequestId` = `"interception-job-1.0"` | `'http://evil.test/landed'` | `['/landed']` | `''` |
| C2 goto 302, failRequest hop | yes | `redirectedRequestId` = `"interception-job-1.0"` | `'chrome-error://chromewebdata/'` | `[]` | `'Error: Page.goto: net::ERR_BLOCKED_BY_CLIENT at http://fixture.test/redirect'` |
| C3 precommit article.html, then C2 | yes | `redirectedRequestId` = `"interception-job-2.0"` | `'chrome-error://chromewebdata/'` | `[]` | `'Error: Page.goto: net::ERR_BLOCKED_BY_CLIENT at http://fixture.test/redirect'` |
| C4 same-origin 302, continue all | yes | `redirectedRequestId` = `"interception-job-1.0"` | `'http://fixture.test/other'` | `[]` | `''` |
| C5 meta refresh, failRequest hop | yes | none | `'chrome-error://chromewebdata/'` | `[]` | `''` |
| C6 click link, failRequest hop | yes | none | `'chrome-error://chromewebdata/'` | `[]` | `''` |
| C7 page.route and CDP both on | yes | `redirectedRequestId` = `"interception-job-1.0"` | `'http://evil.test/landed'` | `['/landed']` | `''` |

Event counts recorded per case:

```
C1  Fetch.requestPaused events: 2   page.route calls: 0
C2  Fetch.requestPaused events: 2   page.route calls: 0
C3  Fetch.requestPaused events: 3   page.route calls: 0
C4  Fetch.requestPaused events: 2   page.route calls: 0
C5  Fetch.requestPaused events: 2   page.route calls: 0
C6  Fetch.requestPaused events: 2   page.route calls: 0
C7  Fetch.requestPaused events: 2   page.route calls: 1
```

The C7 interception order, pasted:

```
interception order:
  Fetch.requestPaused http://fixture.test/redirect
  page.route http://fixture.test/redirect
  Fetch.requestPaused http://evil.test/landed
```

and the single request `page.route` recorded in C7:

```
{"is_navigation_request": true, "redirected_from": null, "resource_type": "document", "url": "http://fixture.test/redirect"}
```

The C1 hop event, pasted in full, is the one that answers the probe's question:

```
{"frameId": "681E76AB1F820701A51B126899AC105E", "networkId": "393622940CCC39FEB2202EAEA6955AD3", "redirectedRequestId": "interception-job-1.0", "request": {"headers": {...}, "initialPriority": "VeryHigh", "method": "GET", "referrerPolicy": "unsafe-url", "url": "http://evil.test/landed"}, "requestId": "interception-job-1.1", "resourceType": "Document"}
```

(headers elided here only for width; the full event is in `probe/cdp/raw.txt`.)

In C5 and C6 the evil.test event carries no `redirectedRequestId` and a
`networkId` different from the initial document's, pasted from C5:

```
initial: "networkId": "F256D3AFB8EFB0A7E8390F20B9592DE1"  url http://fixture.test/meta
hop:     "networkId": "293B1FAB5F9E4E39751591CCA59315D5"  url http://evil.test/landed
```

In C1, C2, C4 and C7 the hop shares the initial request's `networkId`, pasted
from C1:

```
initial: "networkId": "393622940CCC39FEB2202EAEA6955AD3"  requestId "interception-job-1.0"
hop:     "networkId": "393622940CCC39FEB2202EAEA6955AD3"  requestId "interception-job-1.1"
```

## Observed, not interpreted

### Whether Fetch.requestPaused covers every navigation page.route covered, C5 to C7

Direct comparison was recorded only in C7, the one case where both mechanisms
were enabled. In C5 and C6 the probe ran with `page.route also enabled: False`,
so there is no page.route column to compare against in those two.

C7, both enabled:

```
Fetch.requestPaused events: 2
page.route calls: 1
```

The request `page.route` recorded, `http://fixture.test/redirect`, was also
recorded by `Fetch.requestPaused`. The request `Fetch.requestPaused` recorded
that `page.route` did not, `http://evil.test/landed`, is the redirect hop. In
C7 the ordering recorded is Fetch before page.route for the initial document,
and Fetch alone for the hop.

C5 and C6, Fetch only: two events each, the initial document and the
cross-origin target. In both, `failRequest` on the cross-origin target left
`evil.test hits: []`.

### Whether C3 left the page on article.html

No.

```
C3  pre-call page.url: 'http://fixture.test/article.html'
C3  final page.url:    'chrome-error://chromewebdata/'
C3  goto/action error: 'Error: Page.goto: net::ERR_BLOCKED_BY_CLIENT at http://fixture.test/redirect'
C3  evil.test hits:    []
```

The same final URL was recorded in C2, which had no precommitted document:

```
C2  pre-call page.url: 'about:blank'
C2  final page.url:    'chrome-error://chromewebdata/'
```

and in C5 and C6, which used `failRequest` on a navigation that was not an
HTTP redirect:

```
C5  final page.url: 'chrome-error://chromewebdata/'
C6  final page.url: 'chrome-error://chromewebdata/'
```

## fulfillRequest 204

Follow-up measurement. Same origins and patterns; the denied hop is answered
with `Fetch.fulfillRequest` `responseCode` 204 and an empty body instead of
`Fetch.failRequest`. Script: `scratchpad/probe_cdp_204.py`. Raw output is
appended to `probe/cdp/raw.txt`.

| case | shape | pre-call page.url | final page.url | final == pre-call | evil.test hits | error text |
|---|---|---|---|---|---|---|
| C3f | C3 shape: precommit article.html, then goto /redirect (302 to evil.test) | `'http://fixture.test/article.html'` | `'http://fixture.test/article.html'` | `True` | `[]` | `'Error: Page.goto: net::ERR_ABORTED at http://fixture.test/redirect'` |
| C6f | C6 shape: click a link to evil.test | `'http://fixture.test/link.html'` | `'http://fixture.test/link.html'` | `True` | `[]` | `''` |

Against the same shapes answered with `failRequest`, pasted from the cases
above:

```
C3   final page.url: 'chrome-error://chromewebdata/'   evil.test hits: []
C3f  final page.url: 'http://fixture.test/article.html'  evil.test hits: []
C6   final page.url: 'chrome-error://chromewebdata/'   evil.test hits: []
C6f  final page.url: 'http://fixture.test/link.html'   evil.test hits: []
```

The hop in C3f still carried its redirect reference:

```
"redirectedRequestId": "interception-job-2.0"  url http://evil.test/landed
```

and the C6f hop, which is not an HTTP redirect, still carried none.

### Observed, not interpreted

`final page.url == pre-call page.url` was recorded as `True` in C3f and in
C6f. It was `False` in C3 and C6, where the same shapes were answered with
`failRequest`. `evil.test hits` was `[]` in all four.
