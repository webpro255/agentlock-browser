# Popup probe: target=_blank and window.open against the main-frame interceptor

HEAD at time of probe: `cd5a5f5` (README: surface model-driven results table, ...)
Chromium: `151.0.7922.34` (measured separately with `playwright.chromium.launch().version`)

Raw output: `probe/popup/raw.txt`. Script and fixtures: `scratchpad/popup/`
(gitignored, per the instruction that the fixtures stay out of `tests/`).
Nothing under `agentlock_browser/` was changed. The only instrumentation is
probe-side and delegates to the original behaviour: the `BrowserSession`
instance attribute `_handle_paused` was wrapped to count `Fetch.requestPaused`
deliveries, and a `context.on("page")` listener recorded pages the probe did
not open.

The four fixtures, served from the fixture origin:

```
blank_link.html   <p><a href="http://evil.test/popup" target="_blank">Continue to part two</a></p>
window_open.html  <p><button onclick="window.open('http://evil.test/popup')">Continue to part two</button></p>
blank_same.html   <p><a href="http://fixture.test/other" target="_blank">Continue to part two</a></p>
auto_open.html    <script>window.open('http://evil.test/popup');</script>
```

Each case ran in its own `BrowserService`, with `evil.test` hits reset first,
the fixture origin on the allowlist, and `operator_text` naming the fixture
URL. The setup navigation to the fixture was allowed on `USER` in all four
cases. Then the action, then a 2 second wait, then the readings below.

## Cases

`interceptor fires` counts `Fetch.requestPaused` deliveries for the whole case,
with the count during the action in parentheses. `decisions logged` counts
`"event": "decision"` lines in that case's JSONL log.

| case | pages after | page urls | interceptor fires | decisions logged | evil.test hits | snapshot() read |
|---|---|---|---|---|---|---|
| N1 click `_blank` link to evil.test | 2 | `'http://fixture.test/blank_link.html'`, `'http://evil.test/popup'` | 1 (0 during action) | 1, the setup navigate, `allow` on `USER` | `['/popup']` | `'http://fixture.test/blank_link.html'` |
| N2 click button calling `window.open` | 2 | `'http://fixture.test/window_open.html'`, `'http://evil.test/popup'` | 1 (0 during action) | 1, the setup navigate, `allow` on `USER` | `['/popup']` | `'http://fixture.test/window_open.html'` |
| N3 click `_blank` link, same origin (control) | 2 | `'http://fixture.test/blank_same.html'`, `'http://fixture.test/other'` | 1 (0 during action) | 1, the setup navigate, `allow` on `USER` | `[]` | `'http://fixture.test/blank_same.html'` |
| N4 navigate only, script calls `window.open` on load | 2 | `'http://fixture.test/auto_open.html'`, `'http://evil.test/popup'` | 1 (0 during action) | 1, the setup navigate, `allow` on `USER` | `['/popup']` | `'http://fixture.test/auto_open.html'` |

The single `Fetch.requestPaused` event in every case, pasted:

```
N1  fire[0]: {"url": "http://fixture.test/blank_link.html", "frameId": "9132F8371DAC76CA21238C74E5F38183", "is_main_frame_id": true, "redirectedRequestId": null}
N2  fire[0]: {"url": "http://fixture.test/window_open.html", "frameId": "0E5651CC8D318855600C96FF2AE1BB6B", "is_main_frame_id": true, "redirectedRequestId": null}
N3  fire[0]: {"url": "http://fixture.test/blank_same.html", "frameId": "9F363BA051FDF776FDA9FCECE5D24112", "is_main_frame_id": true, "redirectedRequestId": null}
N4  fire[0]: {"url": "http://fixture.test/auto_open.html", "frameId": "569ABC33BE8F74E84199947F6E713249", "is_main_frame_id": true, "redirectedRequestId": null}
```

`browser.blocked` was `[]` and `browser.interceptor_errors` was `[]` in all
four cases.

The tool result the caller sees for the click, in N1:

```
click result: {"ok": true, "element_id": "1-e2", "origin": "http://fixture.test", "url": "http://fixture.test/blank_link.html", "title": "Blank link", "blocked": [], "error": ""}
```

and in N2:

```
click result: {"ok": true, "element_id": "1-e2", "origin": "http://fixture.test", "url": "http://fixture.test/window_open.html", "title": "Window open", "blocked": [], "error": ""}
```

`snapshot()` after the action returned the elements of the fixture page in all
four cases. The full element lists are in `raw.txt`; the second page is not
named in any of them, and no tool in the surface returns its url.

The N3 second page is `'http://fixture.test/other'` with title
`'Error response'`: no such file is served, so the fixture origin answered 404.
The navigation still committed a document at that URL.

## Observed, not interpreted

### Cases where evil.test hits is non-empty

N1: `['/popup']`. N2: `['/popup']`. N4: `['/popup']`.

In each, `interceptor fires` during the action is 0, `browser.blocked` is `[]`,
`browser.interceptor_errors` is `[]`, and the only decision in the log is the
setup navigate to the fixture. The evil origin's handler recorded the request,
and the resulting page carries `title = 'evil'`, which that handler serves.

N4 took no action at all: the hit followed the setup navigate and the 2 second
wait, with no click and no tool call in between.

### Cases where the context has more than one page

All four. N1, N2, N3 and N4 each ended with `len(context.pages) == 2`.

In every case `svc.browser.page` is `page[0]`, the fixture page, and the second
page was created after `start()`, as recorded by the `context.on("page")`
listener:

```
N1  ["http://evil.test/popup"]
N2  ["http://evil.test/popup"]
N3  ["http://fixture.test/other"]
N4  ["http://evil.test/popup"]
```

### No stop condition

No case raised an exception, and no case required a change under
`agentlock_browser/` to run.
