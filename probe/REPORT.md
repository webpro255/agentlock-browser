# Playwright MCP: read-only probe of returned shapes

Purpose: record exactly what the upstream Playwright MCP server returns for browsing
tools, as input to a decision about provenance-tagging granularity. Everything below is
quoted from files in `probe/`. No inference.

## Run configuration

- `@playwright/mcp` **0.0.79** (`node_modules/@playwright/mcp/package.json`), bundling
  `playwright 1.63.0-alpha-2026-08-05`.
- Browser: Playwright chromium 152.0.7977.8 (`npx playwright install chromium`).
  The server's *default* browser channel is `chrome`, which is absent here; the first run
  returned `Error: async createBrowserWithInfo: Chromium distribution 'chrome' is not
  found at /opt/google/chrome/chrome`, so all recorded runs pass `--browser chromium`.
- Server args (main run, `probe/run_probe.py`):
  `npx @playwright/mcp --headless --isolated --no-sandbox --browser chromium --output-dir probe/output`
- Client: Python `mcp` 1.26.0 over stdio.
- Handshake, from `probe/tools.json`:
  `serverInfo: {"name": "Playwright", "version": "1.63.0-alpha-2026-08-05"}`,
  `protocolVersion: 2025-11-25`, capabilities `{"tools": {"listChanged": null}}` only:
  no `resources`, no `prompts`, no `logging`.
- Sites: `https://example.com` (slug `example`), `https://en.wikipedia.org/wiki/Provenance`
  (slug `wikipedia`).

## Saved files

- `probe/tools.json`: initialize result + full `list_tools` output with schemas (24 tools).
- `probe/results/<tool>_<site>.json`: one file per call, each holding
  `{"tool", "arguments", "result"}` where `result` is the verbatim `CallToolResult`.
- Supplementary files, same format:
  `browser_snapshot_second_<site>.json` (immediate re-snapshot, no interaction),
  `refcheck_samepage_<site>.json`, `refcheck_stale_example_ref_on_wikipedia.json`,
  `control_no_outputdir_browser_navigate_<site>.json`,
  `targetprobe_*.json` (ref vs. CSS-selector targeting).
- `probe/output/*.yml`, `probe/output_control/*.yml`: snapshot files the server itself
  wrote to disk (see "browser_navigate" below).

## Result envelope: uniform across every call

Every one of the 23 saved results has exactly one content block, of type `text`:

```json
"content": [ { "type": "text", "text": "...", "annotations": null, "meta": null } ],
"structuredContent": null,
"isError": false
```

`structuredContent` is `null` in all 23 files. `meta` and `annotations` are `null` in all
23 files. None of the 24 tools in `probe/tools.json` declares an `outputSchema`; every
`outputSchema` field is `null`.

So: **nothing arrives as structured JSON.** Every tool result is one Markdown-ish string
with `###` section headers. Sections observed: `### Ran Playwright code`, `### Page`,
`### Snapshot`, `### Result`, `### Error`.

Errors come back as `isError: true` with the message inside the same single text block.
`probe/results/refcheck_stale_example_ref_on_wikipedia.json`:

```json
"text": "### Error\nError: Ref e2 not found in the current page snapshot. Try capturing new snapshot.",
"isError": true
```

`list_tools` does carry per-tool `annotations`, e.g. `browser_snapshot` →
`{"title": "Page snapshot", "readOnlyHint": true, "destructiveHint": false, "openWorldHint": true}`,
while `browser_evaluate` → `{"title": "Evaluate JavaScript", "readOnlyHint": false,
"destructiveHint": true, "openWorldHint": true}`.

## Per-tool observations

### browser_navigate: returns *no page content*, only a file path

`probe/results/browser_navigate_example.json`:

~~~
### Ran Playwright code
```js
await page.goto('https://example.com');
```
### Page
- Page URL: https://example.com/
- Page Title: Example Domain
### Snapshot
- [Snapshot](probe/output/page-2026-08-22T00-53-56-772Z.yml)
~~~

`probe/results/browser_navigate_wikipedia.json` is the same shape:
`- Page URL: https://en.wikipedia.org/wiki/Provenance`,
`- Page Title: Provenance - Wikipedia`,
`- [Snapshot](probe/output/page-2026-08-22T00-54-00-992Z.yml)`.

The auto-snapshot is **written to the server's filesystem and referenced by path**, not
returned over MCP. This is not an artifact of `--output-dir`: the control run with no
`--output-dir` (`probe/results/control_no_outputdir_browser_navigate_example.json`)
returned `- [Snapshot](.playwright-mcp/page-2026-08-22T00-55-24-979Z.yml)`, i.e. the same
behaviour against a default directory. The file content is the same YAML tree that
`browser_snapshot` returns inline. `probe/output/page-2026-08-22T00-53-56-772Z.yml`
begins `- generic [ref=e2]:` and ends `- /url: https://iana.org/domains/example`.

The only page-derived data crossing the MCP boundary on a navigate call is `Page URL` and
`Page Title`.

### browser_snapshot: one text blob containing a YAML accessibility tree

`probe/results/browser_snapshot_example.json` (411 chars of text, 5 refs):

~~~
### Page
- Page URL: https://example.com/
- Page Title: Example Domain
### Snapshot
```yaml
- generic [ref=e2]:
  - heading "Example Domain" [level=1] [ref=e3]
  - paragraph [ref=e4]: This domain is for use in documentation examples without needing permission. Avoid use in operations.
  - paragraph [ref=e5]:
    - link "Learn more" [ref=e6] [cursor=pointer]:
      - /url: https://iana.org/domains/example
```
~~~

`probe/results/browser_snapshot_wikipedia.json`: the single text block is **289,135
characters / 3,879 lines**, containing **1,951 `[ref=` occurrences**, 874 `- text:` lines
and 920 `- /url:` lines.

### Is page text one string or separate identified elements? Both, and the split is uneven

It is one string at the MCP layer (a single text block), but *inside* that string text is
distributed over the tree in two different ways, and **text is never itself addressable**:

1. Inline on the element line when the element has a single text child:
   `- paragraph [ref=e4]: This domain is for use in documentation examples without needing permission. Avoid use in operations.`
   (`browser_snapshot_example.json`).
2. As child `- text:` nodes when the element has mixed content. From
   `browser_snapshot_wikipedia.json`:

```
            - paragraph [ref=f1e251]:
              - text: Provenance (from
              - link "French" [ref=f1e252] [cursor=pointer]:
                - /url: https://en.wikipedia.org/wiki/French_language
              - link "provenir" [ref=f1e255] [cursor=pointer]:
                - /url: https://en.wiktionary.org/wiki/provenir#French
              - text: "'to come from/forth') is the chronology of the ownership, custody or location of a historical object."
              - superscript [ref=f1e256]:
                - link "[1]" [ref=f1e257] [cursor=pointer]:
                  - /url: "#cite_note-1"
              - text: The term was originally mostly used in relation to
              - link "works of art" [ref=f1e258] [cursor=pointer]:
                - /url: https://en.wikipedia.org/wiki/Works_of_art
```

Of the 874 `- text:` lines in that file, **0 carry a `[ref=`**. Refs attach to elements
(`generic`, `heading`, `paragraph`, `link`, `button`, `searchbox`, `superscript`, `img`,
…), never to the text runs themselves. Some element nodes also carry no ref at all:
1,004 node lines in the wikipedia snapshot lack `[ref=`, including repeated bare `- list`
lines and the accessible-name-only entries counted above.

Link destinations *are* per-node: each `link` node is followed by its own `- /url:` line.

No node carries any origin/source attribute. The only provenance-ish data in the whole
payload is the two header lines `- Page URL:` / `- Page Title:` at the top of the result.

### browser_evaluate: text extraction, returns one opaque string, no element identity

`probe/results/browser_evaluate_innerText_example.json`, called with
`{"function": "() => document.body.innerText"}`:

~~~
### Result
"Example Domain\n\nThis domain is for use in documentation examples without needing permission. Avoid use in operations.\n\nLearn more"
### Ran Playwright code
```js
await page.evaluate('() => document.body.innerText');
```
~~~

`probe/results/browser_evaluate_innerText_wikipedia.json` is the same shape, 55,556
characters, one JSON-quoted string:
`"Jump to content\nMain menu\nSearch\nDonate\nCreate account\nLog in\nContents hide\n(Top)\nWorks of art and antiques\n…"`.

The returned value is JSON-serialized into the text block. **No refs, no structure, no
per-element boundaries, and no URL header**: the `### Page` section is absent from
evaluate results. The evaluated page text and the tool's own echoed source code sit in the
same string.

### browser_find: cheaper snapshot search, returns snapshot subtrees with refs

`probe/results/browser_find_example.json`, called with `{"text": "Example"}`:

```
### Result
Found 3 matches for "Example":

- generic [ref=e2]:
  - heading "Example Domain" [level=1] [ref=e3]
  - paragraph [ref=e4]: This domain is for use in documentation examples without needing permission. Avoid use in operations.
  …
```

`probe/results/browser_find_wikipedia.json` (`{"text": "Provenance"}`) opens
`Found 290 matches for "Provenance":` and is 184,994 characters: 134 subtree snippets
separated by `----` lines, each rendered as a path from the tree root down to the match,
carrying the same `[ref=…]` identifiers as `browser_snapshot`. It is a filtered view of
the same tree format, not a distinct result type.

### Element refs: format, lifetime, opacity

Format observed: `e<N>` on example.com's first load (`e2`…`e6`) and `f<M>e<N>` on wikipedia
(`f1e1`…`f1e2173`), a frame prefix plus a per-document counter.

- **Stable across repeated calls on the same page.** `browser_snapshot_wikipedia.json` and
  `browser_snapshot_second_wikipedia.json` are byte-identical (both 296,925 bytes; `diff`
  of the text blocks reports no difference). Same for the two example.com snapshots.
- **Usable in a later, different call.** `probe/results/refcheck_samepage_example.json`
  passed `"target": "e2"` (captured from the earlier snapshot) to `browser_evaluate` and
  got `"DIV|Example DomainThis domain is for use in documentation examples without needing p"`,
  `isError: false`. Same for wikipedia with `"target": "f1e1"` →
  `"BODY|\nJump to content\n…"`.
- **Dead after navigation.** `probe/results/refcheck_stale_example_ref_on_wikipedia.json`
  reused example.com's `e2` after navigating to wikipedia:
  `### Error\nError: Ref e2 not found in the current page snapshot. Try capturing new snapshot.`,
  `isError: true`.
- **Not stable across re-loads of the same URL.** In `probe/results/targetprobe_*`, the
  first load of example.com yielded `['e2','e3','e4','e5','e6']`
  (`targetprobe_snapshot_example_1.json`); after clicking away and re-navigating to the
  same URL, `targetprobe_snapshot_example_2.json` yielded
  `['f2e2','f2e3','f2e4','f2e5','f2e6']`, identical numeric parts, new frame prefix.
  Recorded in `probe/results/_targetprobe_refs.json` as `"identical": false`.
- **Opaque to the caller.** The ref is resolved server-side into a Playwright locator, and
  the echoed code reveals what it became: passing `"target": "e2"` produced
  `await page.getByText('Example DomainThis domain is').evaluate(…)`
  (`refcheck_samepage_example.json`), and passing the link's ref to `browser_click`
  produced `await page.getByRole('link', { name: 'Learn more' }).click();`
  (`targetprobe_click_by_ref_example.json`). The ref string itself carries no role, no
  text, no URL.

### Do click/fill take refs or selectors? Both.

Schema, identically worded for `browser_click`, `browser_hover`, `browser_type`,
`browser_select_option`, `browser_evaluate`, and each item of `browser_fill_form.fields`
(`probe/tools.json`):

```json
"target": {
  "type": "string",
  "description": "Exact target element reference from the page snapshot, or a unique element selector"
}
```

Confirmed empirically on example.com:

- Ref: `targetprobe_click_by_ref_example.json`, `{"target": "e6", "element": "Learn more link (targeted by snapshot ref)"}`
  → `await page.getByRole('link', { name: 'Learn more' }).click();`, `isError: false`,
  landing on `- Page URL: https://www.iana.org/help/example-domains`.
- CSS selector: `targetprobe_click_by_css_selector_example.json`, `{"target": "a", …}`
  → `await page.locator('a').click();`, `isError: false`, same landing URL.
- CSS selector on evaluate: `targetprobe_evaluate_by_css_selector_example.json`,
  `{"target": "h1", …}` → `await page.locator('h1').evaluate(…)`, result `"H1|Example Domain"`.

`browser_click` requires only `target`; `element` (the human-readable description, per the
schema "used to obtain permission to interact with the element") is optional and is not
validated against what `target` resolves to. `browser_fill_form` requires
`target`, `name`, `type`, `value` per field, with `type` ∈
`["textbox","checkbox","radio","combobox","slider"]`.

Post-action results carry the same shape as navigate: echoed code, `Page URL`,
`Page Title`, and a **link to a snapshot file on disk** rather than inline content
(`targetprobe_click_by_ref_example.json` → `- [Snapshot](probe/output/page-2026-08-22T00-56-19-388Z.yml)`).

## Full tool list (24), from `probe/tools.json`

`browser_close`, `browser_resize`, `browser_console_messages`, `browser_handle_dialog`,
`browser_evaluate`, `browser_file_upload`, `browser_drop`, `browser_find`,
`browser_fill_form`, `browser_press_key`, `browser_type`, `browser_navigate`,
`browser_navigate_back`, `browser_network_requests`, `browser_network_request`,
`browser_run_code_unsafe`, `browser_take_screenshot`, `browser_snapshot`, `browser_click`,
`browser_drag`, `browser_hover`, `browser_select_option`, `browser_tabs`,
`browser_wait_for`.

There is no dedicated text/content extraction tool in the list. The tools that return page
content are `browser_snapshot` (tree, inline), `browser_find` (tree subtrees, inline),
`browser_evaluate` (arbitrary JS return value, inline) and `browser_run_code_unsafe`
(not exercised in this probe).
