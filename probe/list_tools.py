import asyncio, json, os
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = StdioServerParameters(
    command="npx",
    args=["@playwright/mcp", "--headless", "--isolated", "--no-sandbox",
          "--output-dir", os.path.abspath("probe/output")],
    env=os.environ.copy(),
)

async def main():
    async with stdio_client(SERVER) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            tools = await s.list_tools()
            out = {
                "server_info": init.model_dump(mode="json"),
                "tools": tools.model_dump(mode="json"),
            }
            with open("probe/tools.json", "w") as f:
                json.dump(out, f, indent=2, sort_keys=False)
            print("TOOL COUNT:", len(tools.tools))
            for t in tools.tools:
                print("-", t.name)

asyncio.run(main())
