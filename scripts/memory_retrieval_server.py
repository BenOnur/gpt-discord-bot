"""Loopback-only warm retrieval bridge to the bot's fixed Basic Memory project.

Run with the existing Basic Memory venv. Exposes search/health only, never the
underlying MCP write/delete tools. Does not load model-provider API keys.
"""
import asyncio
import json
import logging
import os


def search_payload(result):
    if result.get("isError"):
        raise RuntimeError("Basic Memory search failed")
    structured = result.get("structuredContent") or result.get("structured_content")
    if isinstance(structured, dict):
        if "results" in structured:
            return structured
        for key in ("result", "data"):
            value=structured.get(key)
            if isinstance(value, dict) and "results" in value:
                return value
    for block in result.get("content", []):
        if block.get("type") == "text":
            try:
                value=json.loads(block.get("text", ""))
                if isinstance(value, dict) and "results" in value:
                    return value
            except ValueError:
                pass
    raise RuntimeError("Unexpected Basic Memory search response")


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env=os.environ.copy()
    env.update(BASIC_MEMORY_CLOUD_MODE="false", BASIC_MEMORY_AUTO_UPDATE="false", BASIC_MEMORY_LOGFIRE_ENABLED="false")
    parameters=StdioServerParameters(command="/opt/discord-memory-venv/bin/bm", args=["mcp", "--project", "discord-memory"], env=env)
    with open(os.devnull, "w") as errors:
        async with stdio_client(parameters, errlog=errors) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                lock=asyncio.Lock()
                async def search(query, hybrid=True, limit=50):
                    async with lock:
                        result=await session.call_tool("search_notes", arguments={
                            "query":query[:500], "project":"discord-memory", "search_all_projects":False,
                            "search_type":"hybrid" if hybrid else "text", "page_size":min(max(int(limit),1),50), "output_format":"json",
                        })
                        return search_payload(result.model_dump(mode="json", by_alias=True))
                # Pay cold-start cost once before accepting requests.
                await asyncio.wait_for(search("Discord", True, 1), timeout=120)
                async def handle(reader, writer):
                    try:
                        data=await asyncio.wait_for(reader.readline(), 5)
                        if len(data)>4096:
                            raise ValueError("Request too large")
                        request=json.loads(data)
                        if request.get("action")=="health":
                            result={"ready":True}
                        else:
                            if not isinstance(request.get("query"),str) or not request["query"].strip():
                                raise ValueError("A search query is required")
                            result=await asyncio.wait_for(search(request["query"],bool(request.get("hybrid",True)),request.get("limit",50)),8)
                        payload=json.dumps(result,ensure_ascii=False).encode()+b"\n"
                        if len(payload)>1_000_000:
                            raise ValueError("Response too large")
                        writer.write(payload)
                        await writer.drain()
                    except Exception as exc:
                        writer.write(json.dumps({"error":type(exc).__name__}).encode()+b"\n")
                        await writer.drain()
                    finally:
                        writer.close()
                        await writer.wait_closed()
                server=await asyncio.start_server(handle,"127.0.0.1",8801,limit=8192)
                logging.info("Warm Discord memory retrieval ready on loopback")
                async with server:
                    await server.serve_forever()


if __name__=="__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
