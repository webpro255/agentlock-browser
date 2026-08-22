import asyncio, json, os, re
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = "/home/n1trolab/projects/agentlock-browser"
OUT = os.path.join(ROOT, "probe/results")
os.makedirs(OUT, exist_ok=True)

SERVER = StdioServerParameters(
    command="npx",
    args=["@playwright/mcp", "--headless", "--isolated", "--no-sandbox", "--browser", "chromium",
          "--output-dir", os.path.join(ROOT, "probe/output")],
    env=os.environ.copy(),
)

SITES = [
    ("example", "https://example.com"),
    ("wikipedia", "https://en.wikipedia.org/wiki/Provenance"),
]

def save(name, payload):
    p = os.path.join(OUT, name + ".json")
    with open(p, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print("saved", p, os.path.getsize(p), "bytes")

async def call(s, name, args, fname):
    res = await s.call_tool(name, args)
    payload = {"tool": name, "arguments": args, "result": res.model_dump(mode="json")}
    save(fname, payload)
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
            refs = {}
            for slug, url in SITES:
                await call(s, "browser_navigate", {"url": url}, f"browser_navigate_{slug}")
                snap = await call(s, "browser_snapshot", {}, f"browser_snapshot_{slug}")
                txt = first_text(snap)
                found = REF_RE.findall(txt)
                refs[slug] = found
                print(slug, "refs in snapshot:", found[:10], "total", len(found))
                await call(s, "browser_snapshot", {}, f"browser_snapshot_second_{slug}")
                await call(s, "browser_evaluate",
                           {"function": "() => document.body.innerText"},
                           f"browser_evaluate_innerText_{slug}")
                needle = "Example" if slug == "example" else "Provenance"
                await call(s, "browser_find", {"text": needle}, f"browser_find_{slug}")
                if found:
                    await call(s, "browser_evaluate",
                               {"function": "(element) => element.tagName + '|' + (element.textContent||'').slice(0,80)",
                                "element": "element captured from first snapshot of this page",
                                "target": found[0]},
                               f"refcheck_samepage_{slug}")
            if refs.get("example"):
                await call(s, "browser_evaluate",
                           {"function": "(element) => element.tagName",
                            "element": "stale ref captured on example.com",
                            "target": refs["example"][0]},
                           "refcheck_stale_example_ref_on_wikipedia")
            save("_refs_seen", refs)

asyncio.run(main())
