import asyncio, json, os
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = "/home/n1trolab/projects/agentlock-browser"
OUT = os.path.join(ROOT, "probe/results")

SERVER = StdioServerParameters(
    command="npx",
    args=["@playwright/mcp", "--headless", "--isolated", "--no-sandbox", "--browser", "chromium"],
    env=os.environ.copy(),
)

def save(name, payload):
    p = os.path.join(OUT, name + ".json")
    with open(p, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print("saved", p, os.path.getsize(p), "bytes")

async def call(s, name, args, fname):
    res = await s.call_tool(name, args)
    save(fname, {"tool": name, "arguments": args, "result": res.model_dump(mode="json"),
                 "note": "control run: server started WITHOUT --output-dir"})
    return res

async def main():
    async with stdio_client(SERVER) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            await call(s, "browser_navigate", {"url": "https://example.com"},
                       "control_no_outputdir_browser_navigate_example")
            await call(s, "browser_navigate", {"url": "https://en.wikipedia.org/wiki/Provenance"},
                       "control_no_outputdir_browser_navigate_wikipedia")

asyncio.run(main())
