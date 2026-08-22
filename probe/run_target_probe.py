import asyncio, json, os, re
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = "/home/n1trolab/projects/agentlock-browser"
OUT = os.path.join(ROOT, "probe/results")

SERVER = StdioServerParameters(
    command="npx",
    args=["@playwright/mcp", "--headless", "--isolated", "--no-sandbox", "--browser", "chromium",
          "--output-dir", os.path.join(ROOT, "probe/output")],
    env=os.environ.copy(),
)

def save(name, payload):
    p = os.path.join(OUT, name + ".json")
    with open(p, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print("saved", p, os.path.getsize(p), "bytes")

async def call(s, name, args, fname, note=None):
    res = await s.call_tool(name, args)
    d = {"tool": name, "arguments": args, "result": res.model_dump(mode="json")}
    if note: d["note"] = note
    save(fname, d)
    return res

def first_text(res):
    for c in res.content:
        if getattr(c, "type", None) == "text":
            return c.text
    return ""

REF_RE = re.compile(r'\[ref=([^\]]+)\]')

async def main():
    async with stdio_client(SERVER) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            await call(s, "browser_navigate", {"url": "https://example.com"},
                       "targetprobe_navigate_example_1")
            snap1 = await call(s, "browser_snapshot", {}, "targetprobe_snapshot_example_1")
            t1 = first_text(snap1)
            refs1 = REF_RE.findall(t1)
            print("refs pass1:", refs1)
            # click by REF (the "Learn more" link is the last ref)
            await call(s, "browser_click",
                       {"target": refs1[-1], "element": "Learn more link (targeted by snapshot ref)"},
                       "targetprobe_click_by_ref_example",
                       note="target was a snapshot ref string")
            # back to example, snapshot again -> are refs identical after re-navigation?
            await call(s, "browser_navigate", {"url": "https://example.com"},
                       "targetprobe_navigate_example_2")
            snap2 = await call(s, "browser_snapshot", {}, "targetprobe_snapshot_example_2")
            refs2 = REF_RE.findall(first_text(snap2))
            print("refs pass2:", refs2)
            # click by CSS SELECTOR
            await call(s, "browser_click",
                       {"target": "a", "element": "Learn more link (targeted by CSS selector 'a')"},
                       "targetprobe_click_by_css_selector_example",
                       note="target was a CSS selector, not a ref")
            # evaluate with CSS selector target
            await call(s, "browser_navigate", {"url": "https://example.com"},
                       "targetprobe_navigate_example_3")
            await call(s, "browser_evaluate",
                       {"function": "(element) => element.tagName + '|' + element.textContent",
                        "element": "h1 targeted by CSS selector",
                        "target": "h1"},
                       "targetprobe_evaluate_by_css_selector_example",
                       note="target was a CSS selector, not a ref")
            save("_targetprobe_refs", {"pass1": refs1, "pass2": refs2,
                                       "identical": refs1 == refs2})

asyncio.run(main())
