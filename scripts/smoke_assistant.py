"""Opt-in real-provider smoke test, isolated data, no Discord sends.

Run from the repo root with the service environment loaded. Uses the existing
Google provider key but only synthetic prompts and a temporary memory folder.
"""
import ast
import asyncio
import logging
from pathlib import Path
import tempfile
from typing import Any
from types import SimpleNamespace as Obj
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openai import AsyncOpenAI
from bot.assistant_memory import AssistantMemory
from bot.config.loader import get_config
from bot.llm.tools import build_brave_registry, get_openai_tools, execute_tool_call
from bot.llm.tools.discord_ctx import set_current_discord_context
from bot.llm.request_budget import fit_groq_messages, is_small_groq_model
from bot.llm.quota import completion


async def main(config_path):
    function = next(n for n in ast.parse(Path("llmcord.py").read_text()).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_openai_with_tools")
    # Exercise the real engine without executing the module's bot.run() entry.
    namespace = dict(globals())
    exec(compile(ast.Module(body=[function], type_ignores=[]), "smoke-engine", "exec"), namespace)
    run = namespace["run_openai_with_tools"]
    config = get_config(config_path)
    with tempfile.TemporaryDirectory() as folder:
        memory = AssistantMemory({"memory": {"data_dir": folder}})
        channel = Obj(id=20, permissions_for=lambda _: Obj(view_channel=True, read_message_history=True))
        guild = Obj(id=30, text_channels=[channel], threads=[], default_role=Obj(id=0))
        def context(mid, prompt):
            ctx = {"guild": guild, "channel": channel, "author": Obj(id=10),
                   "message": Obj(id=mid, jump_url=f"https://discord.com/channels/30/20/{mid}"),
                   "assistant_memory": memory, "successful_tools": [], "request_text": prompt}
            set_current_discord_context(ctx)
            return ctx
        provider = config["providers"]["google"]
        async with AsyncOpenAI(base_url=provider["base_url"], api_key=provider["api_key"], max_retries=0, timeout=35) as client:
            ctx = context(100, "save coffee preference")
            await run(client, "gemini-3.5-flash-lite", [
                {"role": "system", "content": "Use manage_memory action save kind fact to store this preference. Then confirm."},
                {"role": "user", "content": "I prefer coffee without sugar. Please remember this."},
            ], ["manage_memory"], None, None, {"max_tokens": 600})
            assert memory.entries(ctx)
            ctx = context(101, "coffee preference")
            answer = await run(client, "gemini-3.5-flash-lite", [
                {"role": "system", "content": "Answer only in English from supplied memory." + memory.context(ctx, "coffee")},
                {"role": "user", "content": "Do I prefer coffee with or without sugar?"},
            ], [], None, None, {"max_tokens": 200})
            assert "without sugar" in answer.lower() or "no sugar" in answer.lower() or "unsweetened" in answer.lower(), answer
            for mid in (102, 103):
                ctx = context(mid, "calculate a sum")
                await run(client, "gemini-3.5-flash-lite", [
                    {"role": "system", "content": "Always call calculate to compute the sum. Then answer briefly."},
                    {"role": "user", "content": "Calculate 3+4"},
                ], ["calculate"], None, None, {"max_tokens": 400})
            skills = [row for row in memory.entries(ctx) if row["kind"] == "skill"]
            assert skills, "Repeated successful calls did not promote a skill"
            print({"real_model_save": True, "real_model_recall": True, "real_model_auto_skill": True})


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    asyncio.run(main(os.getenv("CONFIG_PATH", "config.yaml")))
