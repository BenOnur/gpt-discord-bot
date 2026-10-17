"""Real, synthetic-only account/model and two-turn tool smoke; no Discord send."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace as Obj
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from bot.llm.chatgpt_plan import ChatGPTPlanClient
from bot.llm.tools.discord_ctx import set_current_discord_context


async def main():
    path = sys.argv[1]
    set_current_discord_context({"author": Obj(id=1), "guild": Obj(id=1)})
    client = ChatGPTPlanClient({"credentials_path":path, "allowed_guild_ids":[1]})
    token = await client.token()
    async with httpx.AsyncClient(timeout=30) as http:
        response = await http.get("https://api.openai.com/v1/models", headers={"Authorization":"Bearer " + token})
        if response.status_code != 200:
            raise RuntimeError(f"Model catalog HTTP {response.status_code}")
        models = response.json().get("models", [])
        if not any(item.get("slug") == "gpt-6-luna" for item in models):
            raise RuntimeError("Account catalog does not list gpt-6-luna; no other model selected")
    messages = [{"role":"system", "content":"For arithmetic always use calculate before answering."},
                {"role":"user", "content":"Use calculate to find 3 + 4, then answer with the result."}]
    tools = [{"type":"function", "function":{"name":"calculate", "description":"Add two numbers",
              "parameters":{"type":"object", "properties":{"a":{"type":"number"}, "b":{"type":"number"}},
                            "required":["a","b"], "additionalProperties":False}}}]
    first = await client.create(model="gpt-6-luna", messages=messages, tools=tools)
    message = first.choices[0].message
    if not message.tool_calls:
        print("Synthetic smoke reply:", message.content)
        print("Output item types:", [item.get("type") for item in message.model_dump().get("responses_output", [])])
        raise RuntimeError("Tool smoke did not invoke calculate")
    messages.append(message.model_dump(exclude_none=True))
    for call in message.tool_calls:
        if call.function.name != "calculate":
            raise RuntimeError("Unexpected smoke tool")
        import json
        arguments = json.loads(call.function.arguments)
        messages.append({"role":"tool", "tool_call_id":call.id, "content":str(arguments["a"] + arguments["b"])})
    second = await client.create(model="gpt-6-luna", messages=messages, tools=tools)
    if "7" not in (second.choices[0].message.content or ""):
        raise RuntimeError("Tool roundtrip did not return expected result")
    print("PASS: account model gpt-6-luna, low reasoning, Responses inference and tool roundtrip")


if __name__ == "__main__":
    asyncio.run(main())
