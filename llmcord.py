import asyncio
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime
from dotenv import load_dotenv
import logging
from typing import Any, Literal, Optional
import os
import re
import sys

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
import httpx
from openai import AsyncOpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import yaml

# Load .env file at startup
load_dotenv()

from bot.config.loader import get_config as load_config_with_validation
from bot.config.personas import try_load_persona, list_personas
from bot.config.tasks import load_scheduled_tasks
from bot.memory import DiscordMemory
from bot.discord.errors import notify_admin_error as core_notify_admin_error, handle_app_command_error
from bot.llm.errors import parse_error_message
from bot.llm.ollama_service import OllamaService
from bot.llm.tools import get_openai_tools, build_brave_registry, execute_tool_call
from bot.llm.tools.registry import get_tools, build_tool_registry

if os.environ.get("DEBUG"):
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.DEBUG)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

VISION_MODEL_TAGS = ("claude", "gemini", "gemma", "gpt-4", "gpt-5", "grok-4", "llama", "llava", "mistral", "o3", "o4", "vision", "vl")
PROVIDERS_SUPPORTING_USERNAMES = ("openai", "x-ai")
EMBED_COLOR_COMPLETE = discord.Color.dark_green()
EMBED_COLOR_INCOMPLETE = discord.Color.orange()
STREAMING_INDICATOR = " ⚪"
EDIT_DELAY_SECONDS = 1
MAX_MESSAGE_NODES = 500
RESPONSE_TIMEOUT_SECONDS = 60  # per-model timeout; on expiry, fallback models are tried


def strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def get_config(filename: str = "config.yaml") -> dict[str, Any]:
    return load_config_with_validation(filename)


def format_system_prompt(prompt: str, accept_usernames: bool) -> str:
    now = datetime.now().astimezone()
    prompt = prompt.replace("{date}", now.strftime("%B %d %Y")).replace("{time}", now.strftime("%H:%M:%S %Z%z")).strip()
    if accept_usernames:
        prompt += "\n\nUser's names are their Discord IDs and should be typed as '<@ID>'."
    return prompt


def build_openai_client(provider_cfg: dict) -> AsyncOpenAI:
    return AsyncOpenAI(base_url=provider_cfg["base_url"], api_key=provider_cfg.get("api_key", "sk-no-key-required"))


def build_extra_body(provider_cfg: dict, model_params: Any, exclude: set[str] | None = None) -> dict | None:
    base = provider_cfg.get("extra_body") or {}
    params = model_params if isinstance(model_params, dict) else {}
    if exclude:
        params = {k: v for k, v in params.items() if k not in exclude}
    merged = base | params
    return merged if merged else None


config = get_config()
memory = DiscordMemory(config)
curr_model = next(iter(config["models"]))
# Detect initial persona: model-specific > global
model_params = config["models"].get(curr_model, {})
curr_persona = model_params.get("persona") or config.get("persona", "")

logging.info(f"🚀 Bot starting | models: {list(config['models'].keys())} | providers: {list(config['providers'].keys())}")

msg_nodes = {}
processed_messages: set[int] = set()  # Deduplication: track recently processed message IDs
last_task_time = 0
scheduler = AsyncIOScheduler()

intents = discord.Intents.default()
intents.message_content = config.get("enable_message_content", True)
intents.dm_messages = True
intents.voice_states = config.get("enable_voice", True)
activity = discord.CustomActivity(name=(config.get("status_message") or "github.com/jakobdylanc/llmcord")[:128])
discord_bot = commands.Bot(intents=intents, activity=activity, command_prefix=None)
httpx_client = httpx.AsyncClient()


@dataclass
class MsgNode:
    role: Literal["user", "assistant"] = "assistant"

    text: Optional[str] = None
    images: list[dict[str, Any]] = field(default_factory=list)
    role: Literal["user", "assistant"] = "assistant"
    user_id: Optional[int] = None
    has_bad_attachments: bool = False
    fetch_parent_failed: bool = False
    parent_msg: Optional[discord.Message] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


async def notify_admin_error(error: Exception, context: str = "") -> None:
    await core_notify_admin_error(discord_bot, config, error, context)


async def run_ollama(provider_cfg: dict, model: str, model_params: Any, messages: list) -> dict:
    ollama_service = OllamaService(host=provider_cfg["base_url"])
    tools = (model_params or {}).get("tools", []) if isinstance(model_params, dict) else []
    think = (model_params or {}).get("think", False) if isinstance(model_params, dict) else False
    ollama_messages = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if c.get("type") == "text")
        ollama_messages.append({"role": m["role"], "content": content or ""})
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: ollama_service.run(ollama_messages, model, enable_tools=tools, think=think),
    )


async def stream_openai(client: AsyncOpenAI, model: str, messages: list, **kwargs) -> list[str]:
    chunks = []
    async for chunk in await client.chat.completions.create(model=model, messages=messages, stream=True, **kwargs):
        if choice := (chunk.choices[0] if chunk.choices else None):
            if choice.finish_reason:
                chunks.append(choice.delta.content or "")
                break
            chunks.append(choice.delta.content or "")
    return chunks


async def run_openai_with_tools(
    client: AsyncOpenAI,
    model: str,
    messages: list,
    tool_names: list[str],
    extra_headers: Any,
    extra_query: Any,
    extra_body: Any,
    max_tool_chars: int = 8000,
) -> str:
    """
    Non-streaming OpenAI call with full tool call interception loop.
    Works with ANY provider (OpenAI, OpenRouter, etc.) — tools are executed
    bot-side (Brave web search, visuals_core, etc.) and results fed back.
    Returns the final assistant text content.
    """
    registry = build_brave_registry()
    tool_schemas = get_openai_tools(tool_names)
    msgs = list(messages)
    loop_count = 0

    while loop_count < 8:
        loop_count += 1
        create_kw: dict = dict(
            model=model,
            messages=msgs,
            extra_headers=extra_headers,
            extra_query=extra_query,
            extra_body=extra_body,
        )
        if tool_schemas:
            create_kw["tools"] = tool_schemas

        response = await client.chat.completions.create(**create_kw)
        choice = response.choices[0] if response.choices else None
        if not choice:
            break

        msg = choice.message
        # Preserve full message fields including extra_content/thought_signature
        # which Gemini 3.5 requires when returning tool results in multi-turn dialogues.
        if hasattr(msg, "model_dump"):
            assistant_msg = msg.model_dump(exclude_none=True)
            assistant_msg["role"] = "assistant"
            if not assistant_msg.get("content"):
                assistant_msg.pop("content", None)
        else:
            assistant_msg = {"role": "assistant"}
            if getattr(msg, "content", None):
                assistant_msg["content"] = msg.content
            if getattr(msg, "tool_calls", None):
                assistant_msg["tool_calls"] = [
                    tc if isinstance(tc, dict) else {
                        "id": getattr(tc, "id", ""),
                        "type": "function",
                        "function": {
                            "name": getattr(getattr(tc, "function", None), "name", ""),
                            "arguments": getattr(getattr(tc, "function", None), "arguments", ""),
                        },
                        **({"extra_content": getattr(tc, "extra_content", {})} if getattr(tc, "extra_content", None) else {}),
                    }
                    for tc in msg.tool_calls
                ]
        msgs.append(assistant_msg)

        # No tool calls → we have the final answer
        if not msg.tool_calls:
            return msg.content or ""

        # Execute tool calls in a thread so blocking I/O (Brave rate-limit sleep,
        # HTTP requests) doesn't freeze the async event loop.
        # Calls are serialised inside brave_web_search via a threading.Lock,
        # respecting the 1.2s free-tier rate limit even when the model batches
        # multiple tool calls in a single response.
        import json as _json

        async def _run_tool(tc_item) -> dict:
            name = tc_item.function.name
            try:
                args = _json.loads(tc_item.function.arguments or "{}")
            except Exception:
                args = {}
            result = await asyncio.to_thread(
                execute_tool_call, name, args, registry, max_tool_chars
            )
            return {"role": "tool", "tool_call_id": tc_item.id, "content": result}

        # Run sequentially — brave_web_search is already serialised by its lock,
        # so parallel gather() would just queue up anyway and adds no benefit.
        for tc in msg.tool_calls:
            msgs.append(await _run_tool(tc))

    return ""



# ── Slash commands ──────────────────────────────────────────────────────────

@discord_bot.tree.command(name="model", description="View or switch the current model")
async def model_command(interaction: discord.Interaction, model: str) -> None:
    if interaction.user.id not in config["permissions"]["users"]["admin_ids"]:
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    global curr_model
    if model == curr_model:
        out = f"Current model: `{curr_model}`"
    else:
        curr_model = model
        out = f"Model switched to: `{model}`"
        logging.info(out)
    await interaction.response.send_message(out, ephemeral=(interaction.channel.type == discord.ChannelType.private))


@discord_bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception) -> None:
    await handle_app_command_error(interaction, error, discord_bot, config)


@model_command.autocomplete("model")
async def model_autocomplete(interaction: discord.Interaction, curr_str: str) -> list[Choice[str]]:
    global config
    if curr_str == "":
        config = await asyncio.to_thread(get_config)
    choices = [Choice(name=f"◉ {curr_model} (current)", value=curr_model)] if curr_str.lower() in curr_model.lower() else []
    choices += [Choice(name=f"○ {m}", value=m) for m in config["models"] if m != curr_model and curr_str.lower() in m.lower()]
    return choices[:25]


@discord_bot.tree.command(name="clear", description="Clear conversation history and cached messages")
async def clear_command(interaction: discord.Interaction) -> None:
    if interaction.user.id not in config["permissions"]["users"]["admin_ids"]:
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    global msg_nodes, processed_messages
    msg_nodes.clear()
    processed_messages.clear()
    await interaction.response.send_message("✅ Conversation history cleared. Starting fresh!", ephemeral=(interaction.channel.type == discord.ChannelType.private))
    logging.info(f"Cache cleared by {interaction.user.id}")


@discord_bot.tree.command(name="refresh", description="Reload config, tasks, and refresh model/persona")
async def refresh_command(interaction: discord.Interaction) -> None:
    global config, curr_model, curr_persona
    if interaction.user.id not in config["permissions"]["users"]["admin_ids"]:
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    
    try:
        # Reload config (updates global config)
        config = await asyncio.to_thread(get_config)
        
        # Update current model (keep existing if still valid)
        if curr_model not in config["models"]:
            curr_model = next(iter(config["models"]))
        
        # Update current persona (model-specific > global)
        model_params = config["models"].get(curr_model, {})
        curr_persona = model_params.get("persona") or config.get("persona", "")
        
        # Reload scheduled tasks (uses global config)
        setup_scheduled_tasks()
        
        await interaction.response.send_message("✅ Config and tasks reloaded!", ephemeral=True)
        logging.info(f"Config reloaded by {interaction.user.id}")
    except Exception as e:
        logging.error(f"Reload failed: {e}")
        await interaction.response.send_message(f"❌ Reload failed: {e}", ephemeral=True)


@discord_bot.tree.command(name="skill", description="Botun aktif yeteneklerini ve araçlarını listeler")
async def skill_command(interaction: discord.Interaction):
    await interaction.response.defer()
    from bot.llm.tools.registry import get_tools
    tools = get_tools()
    if not tools:
        await interaction.followup.send("❌ Yüklü bir yetenek/araç bulunamadı.")
        return

    items = list(tools.items())
    chunk_size = 11
    embeds = []
    total_pages = (len(items) + chunk_size - 1) // chunk_size

    for i in range(0, len(items), chunk_size):
        chunk = items[i:i + chunk_size]
        page_num = (i // chunk_size) + 1

        embed = discord.Embed(
            title=f"🛠️ Bot Yetenekleri & Araçları ({len(items)} Adet)" if page_num == 1 else "🛠️ Yetenekler & Araçlar (Devamı)",
            color=0x5865F2,
        )
        for name, tool in chunk:
            desc = tool.schema.get("function", {}).get("description", "Açıklama bulunmuyor.")
            if len(desc) > 130:
                desc = desc[:127] + "..."
            embed.add_field(name=f"`{name}`", value=desc, inline=False)

        embed.set_footer(text=f"Sayfa {page_num}/{total_pages} • Toplam {len(items)} araç devrede")
        embeds.append(embed)

    await interaction.followup.send(embeds=embeds)


@discord_bot.tree.command(name="task", description="List activated scheduled tasks")
async def task_command(interaction: discord.Interaction, task: str = None, action: str = None):
    """
    Task management:
    - /task : show all tasks with status
    - /task <name> : toggle task on/off
    - /task <name> run : run task immediately
    """
    global config, scheduler
    if interaction.user.id not in config["permissions"]["users"]["admin_ids"]:
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    
    tasks = load_scheduled_tasks(config)
    # Get loaded jobs from scheduler
    loaded_jobs = {job.id.replace("scheduled_task_", ""): job for job in scheduler.get_jobs() if job.id.startswith("scheduled_task_")}
    
    # If no task specified, show all tasks with status
    if not task:
        if not tasks:
            await interaction.response.send_message("No tasks configured.")
            return
        
        lines = ["**Task Status:**\n"]
        for name, task_config in sorted(tasks.items()):
            status = "ON" if task_config.get("enabled", False) else "OFF"
            cron = task_config.get("cron", "no schedule")
            lines.append(f"- **{name}**: [{status}] `{cron}`")
        
        lines.append("\nUse `/task <name>` to toggle, `/task <name> run` to run immediately.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)
        return
    
    # Check if task exists
    if task not in tasks:
        await interaction.response.send_message(f"Task '{task}' not found.", ephemeral=True)
        return
    
    task_config = tasks[task]
    is_enabled = task_config.get("enabled", False)
    
    # Handle run action
    if action == "run":
        await interaction.response.send_message(f"🔄 Running task '{task}'...", ephemeral=True)
        try:
            result = await run_scheduled_task(task, task_config)
            if result:
                await interaction.followup.send(content=f"**Task '{task}' result:**\n{result[:1900]}", ephemeral=True)
            else:
                await interaction.followup.send(content=f"✅ Task '{task}' completed (no result)", ephemeral=True)
        except Exception as e:
            logging.error(f"Task '{task}' failed: {e}")
            await interaction.followup.send(content=f"❌ Task failed: {e}", ephemeral=True)
        return
    
    # Toggle task (enable/disable)
    # Find and update the task file
    from pathlib import Path
    TASKS_DIR = Path(__file__).parent / "bot" / "config" / "tasks"
    task_file = None
    # Try to find by file name first, then by YAML name field
    for f in TASKS_DIR.glob("*.yaml"):
        # Skip example files
        if "example" in f.stem:
            continue
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        yaml_name = data.get("name", "")
        # Match by file stem or YAML name
        if f.stem.replace("-", "_") == task or yaml_name == task:
            task_file = f
            break
    
    # Toggle task (enable <-> disable)
    new_enabled = not is_enabled
    
    if task_file:
        data = yaml.safe_load(task_file.read_text(encoding="utf-8")) or {}
        data["enabled"] = new_enabled
        task_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    elif "scheduled_tasks" in config and task in config["scheduled_tasks"]:
        config["scheduled_tasks"][task]["enabled"] = new_enabled
    
    if new_enabled:
        setup_scheduled_tasks()
        out = f"✅ Task '{task}' enabled and loaded."
    else:
        job_id = f"scheduled_task_{task}"
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass  # Job might not exist
        out = f"❌ Task '{task}' disabled."
    
    await interaction.response.send_message(out, ephemeral=True)


@task_command.autocomplete("task")
async def task_autocomplete(interaction: discord.Interaction, curr_str: str) -> list[Choice[str]]:
    global config, scheduler
    tasks = load_scheduled_tasks(config)
    loaded_jobs = {job.id.replace("scheduled_task_", ""): job for job in scheduler.get_jobs() if job.id.startswith("scheduled_task_")}
    
    if not tasks:
        return []
    
    if curr_str == "":
        # Show all tasks with ON/OFF status
        return [
            Choice(
                name=f"{name} [ON]" if tasks[name].get("enabled") else f"{name} [OFF]",
                value=name
            )
            for name in tasks.keys()
        ]
    
    # Filter by search string
    return [
        Choice(
            name=f"{name} [ON]" if tasks[name].get("enabled") else f"{name} [OFF]",
            value=name
        )
        for name in tasks.keys() if curr_str.lower() in name.lower()
    ][:25]


@task_command.autocomplete("action")
async def task_action_autocomplete(interaction: discord.Interaction, curr_str: str) -> list[Choice[str]]:
    # Only show "run" as action - toggle is default when no action provided
    actions = ["run"]
    if curr_str == "":
        return [Choice(name=a, value=a) for a in actions]
    return [Choice(name=a, value=a) for a in actions if curr_str.lower() in a.lower()]


@discord_bot.tree.command(name="persona", description="View or switch the current persona")
async def persona_command(interaction: discord.Interaction, persona: str) -> None:
    if interaction.user.id not in config["permissions"]["users"]["admin_ids"]:
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    global curr_persona
    if persona == curr_persona:
        out = f"Current persona: `{curr_persona}`"
    else:
        curr_persona = persona
        out = f"Persona switched to: `{persona}`"
        logging.info(out)
    await interaction.response.send_message(out, ephemeral=(interaction.channel.type == discord.ChannelType.private))


@persona_command.autocomplete("persona")
async def persona_autocomplete(interaction: discord.Interaction, curr_str: str) -> list[Choice[str]]:
    personas = list_personas()
    # Always show current persona first if it matches
    choices = []
    if curr_persona and (curr_str == "" or curr_str.lower() in curr_persona.lower()):
        choices.append(Choice(name=f"◉ {curr_persona} (current)", value=curr_persona))
    # Filter available personas
    if curr_str == "":
        choices += [Choice(name=f"○ {p}", value=p) for p in personas if p != curr_persona]
    else:
        choices += [Choice(name=f"○ {p}", value=p) for p in personas if p and p != curr_persona and curr_str.lower() in p.lower()]
    return choices[:25]


@discord_bot.tree.command(name="quote", description="Generate an Equicord-style quote card for a message or text")
@app_commands.describe(
    message_id="ID or link of the message to quote (optional)",
    text="Custom text to quote (optional)",
    author="Author name (optional)",
)
async def quote_command(
    interaction: discord.Interaction,
    message_id: str = None,
    text: str = None,
    author: str = None,
) -> None:
    await interaction.response.defer()
    target_msg = None
    target_text = text
    target_author = author
    avatar_bytes = None

    if message_id:
        clean_id = message_id.strip().split("/")[-1]
        try:
            mid = int(clean_id)
            target_msg = await interaction.channel.fetch_message(mid)
        except Exception as e:
            await interaction.followup.send(f"❌ Mesaj bulunamadı: {e}", ephemeral=True)
            return

    if target_msg:
        if not target_text:
            target_text = target_msg.clean_content or target_msg.content
        if not target_author:
            target_author = target_msg.author.display_name or target_msg.author.name
        try:
            avatar_bytes = await target_msg.author.display_avatar.with_format("png").with_size(512).read()
        except Exception:
            pass
    elif not target_text:
        await interaction.followup.send("❌ Alıntı yapılacak bir mesaj ID'si veya metin belirtmelisin.", ephemeral=True)
        return

    from bot.quote.renderer import render_quote_card
    try:
        png_bytes = await asyncio.to_thread(
            render_quote_card,
            target_text,
            target_author or interaction.user.display_name,
            avatar_bytes,
        )
        file = discord.File(io.BytesIO(png_bytes), filename="quote.png")
        await interaction.followup.send(file=file)
    except Exception as err:
        await interaction.followup.send(f"❌ Quote oluşturulamadı: {err}", ephemeral=True)


# ── Kimdedi Trivia Slash Commands ──────────────────────────────────────────
@discord_bot.tree.command(
    name="kimdedi",
    description="Bunu Kim Söyledi? Sunucu arşivinden 4 şıklı trivia oyununu başlatır",
)
@app_commands.describe(
    arama="Belirli bir kelimeyi içeren mesajları sormak için opsiyonel arama terimi (örn: cs2, para)",
    siralama="Oyunu başlatmak yerine en çok bilenler liderlik tablosunu gösterir",
)
async def kimdedi_command(
    interaction: discord.Interaction,
    arama: str = None,
    siralama: bool = False,
) -> None:
    data_dir = config.get("memory", {}).get("data_dir", "/var/lib/discord-ai-memory")
    from bot.trivia.game import get_random_trivia, get_trivia_leaderboard, TriviaView

    if siralama:
        board = await asyncio.to_thread(get_trivia_leaderboard, data_dir=data_dir, limit=10)
        if not board:
            await interaction.response.send_message(
                "🏆 Henüz kaydedilmiş bir trivia skoru yok. İlk puanı kapmak için `/kimdedi` başlat!",
                ephemeral=True,
            )
            return

        lines = ["**En Çok Doğru Tahmin Yapanlar:**\n"]
        medals = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 11)]
        for idx, row in enumerate(board):
            m = medals[idx] if idx < len(medals) else f"{idx+1}."
            lines.append(f"{m} **{row['user_name']}**: {row['points']} Puan ({row['wins']} Galibiyet)")

        embed = discord.Embed(
            title="🏆 Bunu Kim Söyledi? - Liderlik Tablosu",
            description="\n".join(lines),
            color=0xFEE75C,
        )
        embed.set_footer(text="Her doğru tahmin +10 Racon Puanı kazandırır.")
        await interaction.response.send_message(embed=embed)
        return

    trivia_data = await asyncio.to_thread(get_random_trivia, data_dir=data_dir, search_query=arama)
    if not trivia_data:
        await interaction.response.send_message(
            "❌ Sunucu arşivinde uygun bir mesaj bulunamadı veya arama kriteriyle eşleşen alıntı yok.",
            ephemeral=True,
        )
        return

    view = TriviaView(trivia_data=trivia_data, data_dir=data_dir)
    embed = view.build_embed()
    await interaction.response.send_message(embed=embed, view=view)
    msg = await interaction.original_response()
    view.message = msg


@discord_bot.tree.command(name="taklit", description="Sunucudaki bir üyenin AI dijital ikizini konuşturur")
@app_commands.describe(
    kullanici="Taklit edilecek sunucu üyesi",
    konu="Klonun konuşacağı konu veya sorulacak soru",
)
async def taklit_command(
    interaction: discord.Interaction,
    kullanici: discord.Member,
    konu: str,
) -> None:
    await interaction.response.defer()
    from bot.clone.simulator import generate_clone_reply, build_clone_embed

    cfg = await asyncio.to_thread(get_config)
    try:
        reply_text = await generate_clone_reply(
            target_user=kullanici,
            topic_or_question=konu,
            config=cfg,
            curr_model=curr_model,
            build_client_fn=build_openai_client,
        )
        embed = build_clone_embed(
            target_user=kullanici,
            question_or_topic=konu,
            clone_response=reply_text,
        )
        await interaction.followup.send(embed=embed)
    except Exception as e:
        logging.error("Failed to generate clone reply: %s", e, exc_info=True)
        await interaction.followup.send(f"❌ Klon konuşturulurken hata oluştu: {e}", ephemeral=True)


# ── Events ───────────────────────────────────────────────────────────────────

@discord_bot.event
async def on_ready() -> None:
    memory.start(discord_bot)
    if client_id := config.get("client_id"):
        logging.info(f"\n\nBOT INVITE URL:\nhttps://discord.com/oauth2/authorize?client_id={client_id}&permissions=412317191168&scope=bot\n")
    # Load voice cog BEFORE syncing commands so its commands are included
    if config.get("enable_voice", True):
        from bot.voice import setup_voice_cog
        await setup_voice_cog(discord_bot, config)
    # Clear any guild-specific commands so each command appears exactly once (no duplicates)
    for guild in discord_bot.guilds:
        try:
            discord_bot.tree.clear_commands(guild=guild)
            await discord_bot.tree.sync(guild=guild)
            logging.info(f"Cleaned up guild command duplicates for: {guild.name} ({guild.id})")
        except Exception as e:
            logging.warning(f"Failed to clear guild commands for {guild.name} ({guild.id}): {e}")

    # Sync global command tree
    await discord_bot.tree.sync()
    commands = discord_bot.tree._get_all_commands()
    logging.info(f"Synced {len(commands)} global slash commands: {[c.name for c in commands]}")
    if not scheduler.running:
        scheduler.start()
        setup_scheduled_tasks()
        logging.info("Scheduler started")
    try:
        from bot.llm.tools.reminder import restore_pending_reminders
        restore_pending_reminders(discord_bot, scheduler)
    except Exception as e:
        logging.warning("Failed to restore reminders: %s", e)

    try:
        from bot.chat.ipc_server import start_ipc_server
        await start_ipc_server(discord_bot)
    except Exception as e:
        logging.warning("Failed to start IPC server: %s", e)


@discord_bot.event
async def on_message_edit(before: discord.Message, after: discord.Message) -> None:
    try:
        await memory.capture(after)
    except Exception as exc:
        logging.warning("Memory edit capture unavailable (%s)", type(exc).__name__)


@discord_bot.event
async def on_message(new_msg: discord.Message) -> None:
    global last_task_time
    try:
        await memory.capture(new_msg)
    except Exception as exc:
        logging.warning("Memory capture unavailable (%s)", type(exc).__name__)

    # CRITICAL: Check if message is from ourselves FIRST, before any processing
    # This must be first to prevent processing our own messages (which trigger on_message when we reply)
    if discord_bot.user and new_msg.author.id == discord_bot.user.id:
        return

    # Deduplication: prevent processing the same message twice
    if new_msg.id in processed_messages:
        logging.warning(f"DUPLICATE_BLOCK: msg_id={new_msg.id} author={new_msg.author.id} already processed")
        return
    processed_messages.add(new_msg.id)
    # Keep set bounded
    if len(processed_messages) > 1000:
        processed_messages.clear()
        processed_messages.add(new_msg.id)

    # DEBUG: Log message start
    logging.info(f"MSG_START: msg_id={new_msg.id} author={new_msg.author.id} channel={new_msg.channel.id}")

    is_dm = new_msg.channel.type == discord.ChannelType.private
    if new_msg.author.bot:
        return

    # Check if the message is a reply to the bot
    is_reply_to_bot = (
        bool(new_msg.reference)
        and bool(getattr(new_msg.reference, "resolved", None))
        and getattr(new_msg.reference.resolved, "author", None) == discord_bot.user
    )
    is_mentioned = discord_bot.user in new_msg.mentions or is_reply_to_bot

    raw_lower = new_msg.content.strip().lower()
    is_text_cmd = raw_lower.startswith((
        "/kimdedi", "!kimdedi",
        "/kimdedi_skor", "!kimdedi_skor",
        "/sıralama", "!sıralama",
        "/siralama", "!siralama",
        "/quote", "!quote",
        "/skor", "!skor",
        "/taklit", "!taklit",
    ))

    if not is_dm and not is_mentioned and not is_text_cmd:
        # Untagged message in server: check zero-token spontaneous banter
        try:
            from bot.chat.spontaneous import maybe_handle_spontaneous
            cfg = await asyncio.to_thread(get_config)
            await maybe_handle_spontaneous(
                new_msg,
                cfg,
                curr_model,
                curr_persona,
                build_openai_client,
                try_load_persona,
            )
        except Exception as exc:
            logging.warning(f"Error in maybe_handle_spontaneous: {exc}")
        return

    role_ids = set(role.id for role in getattr(new_msg.author, "roles", ()))
    channel_ids = set(filter(None, (new_msg.channel.id, getattr(new_msg.channel, "parent_id", None), getattr(new_msg.channel, "category_id", None))))

    config = await asyncio.to_thread(get_config)
    perms = config["permissions"]
    user_is_admin = new_msg.author.id in perms["users"]["admin_ids"]

    (allowed_uids, blocked_uids), (allowed_rids, blocked_rids), (allowed_cids, blocked_cids) = (
        (p["allowed_ids"], p["blocked_ids"]) for p in (perms["users"], perms["roles"], perms["channels"])
    )

    allow_all_users = not allowed_uids if is_dm else not allowed_uids and not allowed_rids
    is_good_user = user_is_admin or allow_all_users or new_msg.author.id in allowed_uids or any(i in allowed_rids for i in role_ids)
    is_bad_user = not is_good_user or new_msg.author.id in blocked_uids or any(i in blocked_rids for i in role_ids)

    allow_all_channels = not allowed_cids
    is_good_channel = user_is_admin or config.get("allow_dms", True) if is_dm else allow_all_channels or any(i in allowed_cids for i in channel_ids)
    is_bad_channel = not is_good_channel or any(i in blocked_cids for i in channel_ids)

    if is_bad_user or is_bad_channel:
        return

    from bot.llm.tools.discord_ctx import set_current_discord_context
    set_current_discord_context({
        "bot": discord_bot,
        "message": new_msg,
        "channel": new_msg.channel,
        "guild": new_msg.guild,
        "author": new_msg.author,
        "scheduler": scheduler,
        "config": config,
    })

    clean_prompt = re.sub(r"<@!?[0-9]+>", "", new_msg.content).strip().lower()

    # Fast-path for quote request:
    # 1) When replying to another message with "@bot quote yap", "alıntıla", "/quote", "!quote"
    # 2) Or typed directly as "/quote <custom text>" / "!quote <custom text>"
    is_quote_on_reply = bool(new_msg.reference and new_msg.reference.message_id) and (
        clean_prompt in {"quote", "quote yap", "bunu quote yap", "alıntı yap", "alıntıla", "quote kartı", "söz kartı", "racon kartı", "/quote", "!quote"}
        or any(clean_prompt.startswith(prefix) for prefix in ("quote yap", "bunu quote yap", "alıntı yap", "quote et", "bunu alıntıla", "/quote", "!quote"))
    )
    is_direct_quote = clean_prompt.startswith(("/quote ", "!quote "))

    if is_quote_on_reply or is_direct_quote:
        from bot.llm.tools.generate_quote import _async_generate_quote
        try:
            custom_text = None
            if is_direct_quote:
                for pfx in ("/quote ", "!quote "):
                    if clean_prompt.startswith(pfx):
                        custom_text = new_msg.content.strip()[len(pfx):].strip()
                        break
            await _async_generate_quote(None, custom_text, {
                "bot": discord_bot,
                "message": new_msg,
                "channel": new_msg.channel,
                "guild": new_msg.guild,
                "author": new_msg.author,
            })
            try:
                await new_msg.add_reaction("🖼️")
            except Exception:
                pass
            return
        except Exception as e:
            logging.error(f"Fast path quote failed: {e}", exc_info=True)

    # Fast-path for trivia leaderboard:
    # "/kimdedi sıralama", "/kimdedi siralama", "!kimdedi sıralama", "!kimdedi siralama",
    # "/kimdedi_skor", "!kimdedi_skor", "/skor", "!skor", "@bot trivia skor"
    is_trivia_score_req = (
        clean_prompt in {
            "kimdedi sıralama", "kimdedi siralama",
            "/kimdedi sıralama", "/kimdedi siralama",
            "!kimdedi sıralama", "!kimdedi siralama",
            "sıralama", "siralama", "/sıralama", "/siralama",
            "kimdedi_skor", "/kimdedi_skor", "!kimdedi_skor",
            "skor", "/skor", "!skor",
            "trivia skor", "trivia skorları", "trivia liderlik", "trivia sıralama",
        }
        or any(clean_prompt.startswith(p) for p in (
            "/kimdedi sıralama", "/kimdedi siralama",
            "!kimdedi sıralama", "!kimdedi siralama",
            "kimdedi sıralama", "kimdedi siralama",
            "/kimdedi_skor", "!kimdedi_skor", "/skor", "!skor",
        ))
    )
    if is_trivia_score_req:
        from bot.llm.tools.trivia import _async_start_trivia
        try:
            await _async_start_trivia(None, True, {
                "bot": discord_bot,
                "message": new_msg,
                "channel": new_msg.channel,
                "guild": new_msg.guild,
                "author": new_msg.author,
                "config": config,
            })
            return
        except Exception as e:
            logging.error(f"Fast path trivia score failed: {e}", exc_info=True)

    # Fast-path for trivia game:
    # e.g. "/kimdedi", "!kimdedi", "/kimdedi oyna", "!kimdedi oyna", "@bot kim dedi", "bunu kim dedi", "trivia başlat"
    is_trivia_req = (
        clean_prompt in {
            "kim dedi", "bunu kim dedi", "kim söyledi", "trivia", "trivia başlat",
            "kim dedi oyunu", "oyun başlat",
            "/kimdedi", "!kimdedi",
            "/kimdedi oyna", "!kimdedi oyna",
            "/kimdedi başlat", "!kimdedi başlat",
            "/kimdedi baslat", "!kimdedi baslat",
        }
        or any(clean_prompt.startswith(p) for p in (
            "/kimdedi oyna", "!kimdedi oyna",
            "/kimdedi başlat", "!kimdedi başlat",
            "/kimdedi baslat", "!kimdedi baslat",
            "/kimdedi", "!kimdedi",
            "kim dedi", "bunu kim dedi", "kim söyledi", "trivia başlat",
        ))
    )
    if is_trivia_req:
        search_query = None
        for prefix in (
            "/kimdedi oyna", "!kimdedi oyna",
            "/kimdedi başlat", "!kimdedi başlat",
            "/kimdedi baslat", "!kimdedi baslat",
            "/kimdedi", "!kimdedi",
            "kim dedi", "bunu kim dedi", "kim söyledi", "trivia başlat",
        ):
            if clean_prompt.startswith(prefix):
                remainder = clean_prompt[len(prefix):].strip()
                if remainder:
                    search_query = remainder
                break

        from bot.llm.tools.trivia import _async_start_trivia
        try:
            await _async_start_trivia(search_query, False, {
                "bot": discord_bot,
                "message": new_msg,
                "channel": new_msg.channel,
                "guild": new_msg.guild,
                "author": new_msg.author,
                "config": config,
            })
            return
        except Exception as e:
            logging.error(f"Fast path trivia failed: {e}", exc_info=True)

    # Fast-path for AI clone impersonation:
    # e.g. "/taklit @kullanıcı <konu>", "!taklit @kullanıcı <konu>", "@bot taklit et @kullanıcı <konu>"
    is_taklit_req = clean_prompt.startswith(("/taklit", "!taklit", "taklit et", "taklit"))
    if is_taklit_req:
        target_member = None
        for m in new_msg.mentions:
            if m.id != discord_bot.user.id:
                target_member = m
                break

        topic = ""
        raw_cmd = new_msg.content.strip()
        for pfx in ("/taklit", "!taklit", "taklit et", "taklit"):
            if clean_prompt.startswith(pfx):
                remainder = raw_cmd[len(pfx):].strip()
                if target_member:
                    remainder = re.sub(r"<@!?" + str(target_member.id) + r">", "", remainder).strip()
                topic = remainder
                break

        if not target_member and new_msg.guild:
            words = raw_cmd.split()
            if len(words) >= 3:
                candidate = words[1].lower().lstrip("@")
                for gm in new_msg.guild.members:
                    if candidate in (gm.name.lower(), gm.display_name.lower()):
                        target_member = gm
                        topic = " ".join(words[2:])
                        break

        if target_member and topic:
            from bot.clone.simulator import generate_clone_reply, build_clone_embed
            try:
                async with new_msg.channel.typing():
                    reply_text = await generate_clone_reply(
                        target_user=target_member,
                        topic_or_question=topic,
                        config=config,
                        curr_model=curr_model,
                        build_client_fn=build_openai_client,
                    )
                    embed = build_clone_embed(
                        target_user=target_member,
                        question_or_topic=topic,
                        clone_response=reply_text,
                    )
                    await new_msg.reply(embed=embed)
                    return
            except Exception as e:
                logging.error(f"Fast path taklit failed: {e}", exc_info=True)


    provider_slash_model = curr_model
    provider, model = provider_slash_model.removesuffix(":vision").split("/", 1)
    provider_cfg = config["providers"][provider]
    model_params = config["models"].get(provider_slash_model)

    logging.info(f"━━━ {provider_slash_model} | params: {model_params}")

    accept_images = any(x in provider_slash_model.lower() for x in VISION_MODEL_TAGS)
    accept_usernames = any(provider_slash_model.lower().startswith(x) for x in PROVIDERS_SUPPORTING_USERNAMES)
    max_text = config.get("max_text", 100000)
    max_images = config.get("max_images", 5) if accept_images else 0
    max_messages = config.get("max_messages", 25)

    # Build message chain
    messages, user_warnings, curr_msg = [], set(), new_msg
    while curr_msg and len(messages) < max_messages:
        curr_node = msg_nodes.setdefault(curr_msg.id, MsgNode())
        async with curr_node.lock:
            if curr_node.text is None:
                cleaned = curr_msg.content.removeprefix(discord_bot.user.mention).lstrip()
                good_att = [a for a in curr_msg.attachments if a.content_type and any(a.content_type.startswith(x) for x in ("text", "image"))]
                att_resps = await asyncio.gather(
                    *[httpx_client.get(a.url) for a in good_att],
                    return_exceptions=True,
                )
                
                # Handle voice message attachments (audio/ogg)
                voice_attachments = [a for a in curr_msg.attachments if a.content_type and a.content_type.startswith("audio")]
                voice_transcriptions = []
                if voice_attachments and config.get("enable_voice", True):
                    # Try to get STT service
                    from bot.voice import create_stt, get_voice_config
                    voice_config = get_voice_config(config)
                    stt_service = create_stt(voice_config) if voice_config else None
                    
                    if stt_service:
                        for va in voice_attachments:
                            try:
                                audio_resp = await httpx_client.get(va.url)
                                if audio_resp.status_code == 200:
                                    transcribed = stt_service.transcribe(audio_resp.content)
                                    if transcribed:
                                        voice_transcriptions.append(f"[Voice message: {transcribed}]")
                            except Exception as e:
                                logging.warning(f"Failed to transcribe voice message: {e}")
                    else:
                        logging.debug("STT not configured, voice messages will be ignored")
                
                curr_node.text = "\n".join(
                    ([cleaned] if cleaned else [])
                    + ["\n".join(filter(None, (e.title, e.description, e.footer.text))) for e in curr_msg.embeds]
                    + [c.content for c in curr_msg.components if c.type == discord.ComponentType.text_display]
                    + [
                        r.text
                        for a, r in zip(good_att, att_resps)
                        if not isinstance(r, Exception) and a.content_type.startswith("text")
                    ]
                    + voice_transcriptions
                )
                curr_node.images = [
                    dict(type="image_url", image_url=dict(url=f"data:{a.content_type};base64,{b64encode(r.content).decode()}"))
                    for a, r in zip(good_att, att_resps)
                    if not isinstance(r, Exception) and a.content_type.startswith("image")
                ]
                curr_node.role = "assistant" if curr_msg.author == discord_bot.user else "user"
                curr_node.user_id = curr_msg.author.id if curr_node.role == "user" else None
                curr_node.has_bad_attachments = len(curr_msg.attachments) > len(good_att) or any(
                    isinstance(r, Exception) for r in att_resps
                )

                try:
                    if (
                        curr_msg.reference is None
                        and discord_bot.user.mention not in curr_msg.content
                        and (prev := ([m async for m in curr_msg.channel.history(before=curr_msg, limit=1)] or [None])[0])
                        and prev.type in (discord.MessageType.default, discord.MessageType.reply)
                        and prev.author == (discord_bot.user if is_dm else curr_msg.author)
                    ):
                        curr_node.parent_msg = prev
                    else:
                        is_thread = curr_msg.channel.type == discord.ChannelType.public_thread
                        is_thread_start = is_thread and curr_msg.reference is None and curr_msg.channel.parent.type == discord.ChannelType.text
                        if pid := curr_msg.channel.id if is_thread_start else getattr(curr_msg.reference, "message_id", None):
                            curr_node.parent_msg = (
                                curr_msg.channel.starter_message or await curr_msg.channel.parent.fetch_message(pid)
                            ) if is_thread_start else (
                                curr_msg.reference.cached_message or await curr_msg.channel.fetch_message(pid)
                            )
                except (discord.NotFound, discord.HTTPException):
                    logging.exception("Error fetching parent message")
                    curr_node.fetch_parent_failed = True

            content = (
                ([dict(type="text", text=curr_node.text[:max_text])] if curr_node.text[:max_text] else []) + curr_node.images[:max_images]
                if curr_node.images[:max_images] else curr_node.text[:max_text]
            )
            if content != "":
                msg = dict(content=content, role=curr_node.role)
                if accept_usernames and curr_node.user_id:
                    msg["name"] = str(curr_node.user_id)
                messages.append(msg)

            if len(curr_node.text) > max_text: user_warnings.add(f"⚠️ Max {max_text:,} characters per message")
            if len(curr_node.images) > max_images: user_warnings.add(f"⚠️ Max {max_images} image{'s' if max_images != 1 else ''} per message" if max_images > 0 else "⚠️ Can't see images")
            if curr_node.has_bad_attachments: user_warnings.add("⚠️ Unsupported attachments")
            if curr_node.fetch_parent_failed or (curr_node.parent_msg and len(messages) == max_messages):
                user_warnings.add(f"⚠️ Only using last {len(messages)} message{'s' if len(messages) != 1 else ''}")
            
            # Stop following parent chain if we reach a bot message to prevent feedback loop
            # This prevents the bot from seeing its own previous responses and responding to them
            if curr_msg.author == discord_bot.user:
                break
            
            curr_msg = curr_node.parent_msg

    logging.info(f"Message (uid:{new_msg.author.id}, att:{len(new_msg.attachments)}, len:{len(messages)})")

    # System prompt: model-specific > current > global
    sys_prompt = ""
    if isinstance(model_params, dict):
        persona_name = model_params.get("persona")
        if persona_name:
            sys_prompt = try_load_persona(persona_name) or ""
        if not sys_prompt:
            sys_prompt = model_params.get("system_prompt") or ""
    if not sys_prompt and curr_persona:
        sys_prompt = try_load_persona(curr_persona) or ""
    if not sys_prompt:
        global_persona = config.get("persona")
        if global_persona:
            sys_prompt = try_load_persona(global_persona) or ""
        if not sys_prompt:
            sys_prompt = config.get("system_prompt") or ""
    if sys_prompt:
        sys_prompt = format_system_prompt(sys_prompt, accept_usernames)

    # Build API messages
    try:
        sys_prompt += await asyncio.wait_for(memory.recall(new_msg), timeout=20)
    except Exception as exc:
        logging.warning("Memory recall unavailable (%s)", type(exc).__name__)
    api_messages = []
    for m in messages[::-1]:
        if isinstance(m.get("content"), list):
            if accept_images:
                api_messages.append(m)
            else:
                api_messages.append({"role": m["role"], "content": " ".join(c.get("text", "") for c in m["content"] if c.get("type") == "text")})
        else:
            api_messages.append(m)
    if sys_prompt:
        api_messages.insert(0, {"role": "system", "content": sys_prompt})

    # Response state
    curr_content = finish_reason = None
    response_msgs, response_contents = [], []
    use_plain = config.get("use_plain_responses", False)
    show_color = config.get("show_embed_color", True)
    max_len = 2000 if use_plain else 4096 - len(STREAMING_INDICATOR)

    if not use_plain:
        embed = discord.Embed.from_dict(dict(fields=[dict(name=w, value="", inline=False) for w in sorted(user_warnings)]))
        if not show_color:
            embed.color = None

    async def reply_helper(**kw) -> None:
        target = new_msg if not response_msgs else response_msgs[-1]
        msg = await target.reply(**kw)
        response_msgs.append(msg)
        msg_nodes[msg.id] = MsgNode(parent_msg=new_msg)
        await msg_nodes[msg.id].lock.acquire()
        # DEBUG: Log every reply sent
        content_preview = str(kw.get('content', ''))[:30] or (str(kw.get('embed').description)[:30] if kw.get('embed') else 'N/A')
        logging.warning(f"REPLY_SENT: #{len(response_msgs)} reply_msg_id={msg.id} target_msg_id={target.id} content='{content_preview}...'")

    # ── Helper: run one OpenAI-compat model with streaming ──────────────────

    async def run_openai_stream(
        client: AsyncOpenAI,
        model_name: str,
        msgs: list,
        tools: list,
        extra_headers: Any,
        extra_query: Any,
        extra_body: Any,
    ) -> None:
        """Stream one OpenAI-compat model into response_contents. Raises on error/timeout."""
        nonlocal curr_content, finish_reason
        curr_content = finish_reason = None

        create_kw: dict = dict(
            model=model_name, messages=msgs, stream=True,
            extra_headers=extra_headers, extra_query=extra_query, extra_body=extra_body,
        )
        if tools:
            create_kw["tools"] = tools

        async def _stream() -> None:
            nonlocal curr_content, finish_reason
            async for chunk in await client.chat.completions.create(**create_kw):
                if finish_reason:
                    break
                choice = chunk.choices[0] if chunk.choices else None
                if not choice:
                    continue
                finish_reason = choice.finish_reason
                prev, curr_content = curr_content or "", choice.delta.content or ""
                new_content = prev if not finish_reason else prev + curr_content
                if not response_contents and not new_content:
                    continue

                if start_next := not response_contents or len(response_contents[-1] + new_content) > max_len:
                    response_contents.append("")
                response_contents[-1] += new_content

                if not use_plain:
                    td = datetime.now().timestamp() - last_task_time
                    split_incoming = not finish_reason and len(response_contents[-1] + curr_content) > max_len
                    is_final = finish_reason or split_incoming
                    is_good = finish_reason and finish_reason.lower() in ("stop", "end_turn")
                    if start_next or td >= EDIT_DELAY_SECONDS or is_final:
                        embed.description = strip_thinking(response_contents[-1]) if is_final else response_contents[-1] + STREAMING_INDICATOR
                        if show_color:
                            embed.color = EMBED_COLOR_COMPLETE if split_incoming or is_good else EMBED_COLOR_INCOMPLETE
                        if start_next:
                            await reply_helper(embed=embed, silent=True)
                        else:
                            await asyncio.sleep(EDIT_DELAY_SECONDS - td)
                            await response_msgs[-1].edit(embed=embed)
                        last_task_time = datetime.now().timestamp()

        async with new_msg.channel.typing():
            await asyncio.wait_for(_stream(), timeout=RESPONSE_TIMEOUT_SECONDS)

        if use_plain:
            for chunk in response_contents:
                if c := strip_thinking(chunk):
                    await reply_helper(content=c)

    # ── Helper: run one Ollama model ────────────────────────────────────────

    async def run_ollama_model(p_cfg: dict, mdl: str, params: Any) -> None:
        """Run Ollama model into response_contents. Raises on error/timeout."""
        async with new_msg.channel.typing():
            result = await asyncio.wait_for(
                run_ollama(p_cfg, mdl, params, api_messages),
                timeout=RESPONSE_TIMEOUT_SECONDS,
            )
        text = strip_thinking(result.get("content", ""))
        chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)] if text else []
        response_contents.extend(chunks)

        if use_plain:
            for chunk in chunks:
                if c := strip_thinking(chunk):
                    await reply_helper(content=c)
        else:
            if not chunks:
                response_contents.append("(No response)")
                chunks = response_contents[-1:]
            for idx, chunk in enumerate(chunks):
                if idx == 0:
                    e = embed if len(chunks) == 1 else discord.Embed.from_dict(
                        dict(fields=[dict(name=w, value="", inline=False) for w in sorted(user_warnings)])
                    )
                    e.description = strip_thinking(chunk)
                    if show_color:
                        e.color = EMBED_COLOR_COMPLETE
                    await reply_helper(embed=e)
                else:
                    await reply_helper(content=strip_thinking(chunk))

    # ── Build full model list (primary + fallbacks) ─────────────────────────

    model_fallbacks = (model_params or {}).get("fallback_models", []) if isinstance(model_params, dict) else []
    global_fallbacks = config.get("fallback_models", []) or []
    all_models = [provider_slash_model] + (model_fallbacks or []) + global_fallbacks

    # ── Try each model in order ─────────────────────────────────────────────

    for attempt_idx, attempt_model in enumerate(all_models):
        if not attempt_model or not attempt_model.strip():
            continue
        is_primary = attempt_idx == 0
        try:
            a_provider, a_model = attempt_model.removesuffix(":vision").split("/", 1)
            a_cfg = provider_cfg if is_primary else config["providers"][a_provider]
            a_params = model_params if is_primary else config["models"].get(attempt_model)

            if a_provider == "ollama":
                await run_ollama_model(a_cfg, a_model, a_params)
            else:
                a_client = build_openai_client(a_cfg) if not is_primary else build_openai_client(provider_cfg)
                a_params_dict = a_params if isinstance(a_params, dict) else {}
                tool_names = a_params_dict.get("tools", [])
                a_extra_body = build_extra_body(a_cfg, a_params, exclude={"tools", "system_prompt", "supports_tools"})
                a_extra_headers = a_cfg.get("extra_headers")
                a_extra_query = a_cfg.get("extra_query")
                response_contents.clear()

                if tool_names:
                    # Tool-calling path: non-streaming, bot executes tools (Brave etc.)
                    async with new_msg.channel.typing():
                        text = await asyncio.wait_for(
                            run_openai_with_tools(
                                a_client, a_model, api_messages, tool_names,
                                a_extra_headers, a_extra_query, a_extra_body,
                            ),
                            timeout=RESPONSE_TIMEOUT_SECONDS,
                        )

                    from bot.llm.tools.discord_ctx import get_current_discord_context
                    ctx = get_current_discord_context()
                    if ctx.get("suppress_chat_reply"):
                        try:
                            rx = ctx.get("suppress_reaction", "🔊")
                            await new_msg.add_reaction(rx)
                        except Exception:
                            pass
                        try:
                            from bot.chat.spontaneous import spontaneous_manager
                            spontaneous_manager.record_bot_activity(new_msg.channel.id)
                        except Exception:
                            pass
                        break

                    if text:
                        chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]
                        response_contents.extend(chunks)
                        if use_plain:
                            for chunk in chunks:
                                if c := strip_thinking(chunk):
                                    await reply_helper(content=c)
                        else:
                            for idx, chunk in enumerate(chunks):
                                e = embed if idx == 0 else discord.Embed()
                                e.description = strip_thinking(chunk)
                                if show_color:
                                    e.color = EMBED_COLOR_COMPLETE
                                await reply_helper(embed=e)
                else:
                    # No tools: normal streaming path
                    await run_openai_stream(
                        a_client, a_model, api_messages, [],
                        a_extra_headers, a_extra_query, a_extra_body,
                    )

            if not is_primary:
                logging.info(f"Fallback '{attempt_model}' succeeded")
            break  # success

        except asyncio.TimeoutError:
            logging.warning(
                f"{'Primary' if is_primary else 'Fallback'} model '{attempt_model}' "
                f"timed out after {RESPONSE_TIMEOUT_SECONDS}s."
                + (" Trying next fallback..." if attempt_idx < len(all_models) - 1 else " No more fallbacks.")
            )
            if attempt_idx == len(all_models) - 1:
                await notify_admin_error(
                    TimeoutError(f"All models timed out after {RESPONSE_TIMEOUT_SECONDS}s"),
                    f"Timeout in #{getattr(new_msg.channel, 'name', 'DM')}",
                )
                try:
                    await new_msg.reply("所有模型皆逾時，已通知管理員。請稍後再試。")
                except Exception:
                    pass

        except Exception as e:
            logging.warning(f"{'Primary' if is_primary else 'Fallback'} model '{attempt_model}' failed: {parse_error_message(e)}")
            if attempt_idx == len(all_models) - 1:
                logging.exception("All models failed")
                await notify_admin_error(e, f"All models failed in #{getattr(new_msg.channel, 'name', 'DM')}")
                try:
                    await new_msg.reply("所有模型皆無法回應，已通知管理員。請稍後再試。")
                except Exception:
                    pass

    for rm in response_msgs:
        msg_nodes[rm.id].text = strip_thinking("".join(response_contents))
        msg_nodes[rm.id].lock.release()

    if response_msgs:
        try:
            from bot.chat.spontaneous import spontaneous_manager
            spontaneous_manager.record_bot_activity(new_msg.channel.id)
        except Exception:
            pass

    if (n := len(msg_nodes)) > MAX_MESSAGE_NODES:
        for mid in sorted(msg_nodes.keys())[:n - MAX_MESSAGE_NODES]:
            async with msg_nodes.setdefault(mid, MsgNode()).lock:
                msg_nodes.pop(mid, None)

    # DEBUG: Log message completion
    logging.info(f"MSG_END: msg_id={new_msg.id} responses_sent={len(response_msgs)} total_processed={len(processed_messages)}")


# ── Scheduled tasks ──────────────────────────────────────────────────────────

async def run_scheduled_task(task_name: str, task_config: dict[str, Any]) -> str | None:
    if not task_config.get("enabled", False):
        return

    channel_id = task_config.get("channel_id")
    user_id = task_config.get("user_id")
    model_name = task_config.get("model") or curr_model
    prompt = task_config.get("prompt", "Check my emails")

    if channel_id:
        target = discord_bot.get_channel(channel_id)
        if not target:
            logging.warning(f"Task '{task_name}': channel {channel_id} not found"); return
    elif user_id:
        target = discord_bot.get_user(user_id)
        if not target:
            try: target = await discord_bot.fetch_user(user_id)
            except discord.NotFound:
                logging.warning(f"Task '{task_name}': user {user_id} not found"); return
        if target.bot:
            logging.warning(f"Task '{task_name}': cannot DM bots"); return
    else:
        logging.warning(f"Task '{task_name}': no channel_id or user_id"); return

    provider, model = model_name.removesuffix(":vision").split("/", 1)
    provider_cfg = config["providers"][provider]
    model_params = config["models"].get(model_name)

    logging.info(f"━━━ Task '{task_name}' | {model_name} | params: {model_params}")

    # System prompt resolution
    sys_prompt = ""
    # Prioritize system_prompt directly from task_config
    if task_config.get("system_prompt"):
        sys_prompt = task_config.get("system_prompt")
    # If no system_prompt in task_config, then try to load from persona
    elif task_config.get("persona"):
        sys_prompt = try_load_persona(task_config.get("persona")) or ""
    if not sys_prompt and isinstance(model_params, dict):
        model_persona = model_params.get("persona")
        if model_persona:
            sys_prompt = try_load_persona(model_persona) or ""
        if not sys_prompt:
            sys_prompt = model_params.get("system_prompt") or ""
    if not sys_prompt:
        global_persona = config.get("persona")
        if global_persona:
            sys_prompt = try_load_persona(global_persona) or ""
        if not sys_prompt:
            sys_prompt = config.get("system_prompt", "")
    if sys_prompt:
        sys_prompt = format_system_prompt(sys_prompt, False)

    task_messages = []
    if sys_prompt:
        task_messages.append({"role": "system", "content": sys_prompt})
    task_messages.append({"role": "user", "content": prompt})

    # Merge task-level tool/think overrides into effective params
    effective_params = dict(model_params) if isinstance(model_params, dict) else {}
    if "tools" in task_config:
        effective_params["tools"] = task_config["tools"]
    if "think" in task_config and provider == "ollama":
        effective_params["think"] = task_config["think"]

    response_text = ""
    try:
        if provider == "ollama":
            result = await asyncio.wait_for(
                run_ollama(provider_cfg, model, effective_params, task_messages),
                timeout=RESPONSE_TIMEOUT_SECONDS,
            )
            response_text = strip_thinking(result.get("content", ""))
        else:
            task_fallbacks = task_config.get("fallback_models") or []
            global_fallbacks = config.get("fallback_models", []) or []
            fallback_models = task_fallbacks + global_fallbacks
            task_extra_body = build_extra_body(provider_cfg, effective_params, exclude={"tools", "system_prompt"})
            task_tool_names = (effective_params or {}).get("tools") or []
            models_to_try = [(model, build_openai_client(provider_cfg), model_name,
                              provider_cfg.get("extra_headers"), provider_cfg.get("extra_query"),
                              task_extra_body, task_tool_names)]

            for fb in fallback_models:
                if not fb or not str(fb).strip(): continue
                try:
                    fp, fm = fb.removesuffix(":vision").split("/", 1)
                    if fp == "ollama": continue
                    fc = config["providers"][fp]
                    fmp = config["models"].get(fb)
                    fmp_extra = build_extra_body(fc, fmp, exclude={"tools", "system_prompt"})
                    # Use task-level tools override if available, otherwise fall back to model-level tools
                    fmp_tools = task_tool_names if task_tool_names else ((fmp or {}).get("tools") or [] if isinstance(fmp, dict) else [])
                    models_to_try.append((fm, build_openai_client(fc), fb, fc.get("extra_headers"), fc.get("extra_query"), fmp_extra, fmp_tools))
                except Exception as se:
                    logging.warning(f"Task '{task_name}': fallback setup failed for '{fb}': {se}")

            for ai, (am, ac, amn, aeh, aeq, aeb, atools_names) in enumerate(models_to_try):
                try:
                    response_text = ""

                    if atools_names:
                        # Tool-calling path: bot executes tools (Brave etc.)
                        response_text = await asyncio.wait_for(
                            run_openai_with_tools(
                                ac, am, task_messages, atools_names,
                                aeh, aeq, aeb,
                            ),
                            timeout=RESPONSE_TIMEOUT_SECONDS,
                        )
                    else:
                        # No tools: plain streaming
                        create_kw = dict(model=am, messages=task_messages, stream=True,
                                         extra_headers=aeh, extra_query=aeq, extra_body=aeb)

                        async def _task_stream() -> None:
                            nonlocal response_text
                            async for chunk in await ac.chat.completions.create(**create_kw):
                                if choice := (chunk.choices[0] if chunk.choices else None):
                                    response_text += choice.delta.content or ""

                        await asyncio.wait_for(_task_stream(), timeout=RESPONSE_TIMEOUT_SECONDS)

                    if ai > 0: logging.info(f"Task '{task_name}': fallback '{amn}' succeeded")
                    break

                except asyncio.TimeoutError:
                    logging.warning(f"Task '{task_name}': {'primary' if ai == 0 else f'fallback [{ai}]'} '{amn}' timed out after {RESPONSE_TIMEOUT_SECONDS}s")
                    if ai == len(models_to_try) - 1: raise
                except Exception as e:
                    logging.warning(f"Task '{task_name}': {'primary' if ai == 0 else f'fallback [{ai}]'} failed: {parse_error_message(e)}")
                    if ai == len(models_to_try) - 1: raise

        max_send = 2000  # Discord limit
        if response_text:
            for i in range(0, len(response_text), max_send):
                try:
                    await target.send(response_text[i:i+max_send])
                except discord.Forbidden:
                    logging.error(f"Task '{task_name}': Cannot DM user {user_id} (DMs disabled/blocked)"); return
            logging.info(f"Task '{task_name}' sent to {'channel ' + str(channel_id) if channel_id else 'user ' + str(user_id)}")
        else:
            await target.send(f"📧 No response from task '{task_name}'")

    except (asyncio.TimeoutError, discord.errors.HTTPException, Exception) as e:
        if isinstance(e, asyncio.TimeoutError):
            logging.error(f"Task '{task_name}': all models timed out")
            await notify_admin_error(TimeoutError(f"Task '{task_name}' timed out"), f"Task '{task_name}' timeout")
        elif isinstance(e, discord.errors.HTTPException) and e.code == 50007:
            logging.error(f"Task '{task_name}': Cannot DM user {user_id} (error 50007)")
        else:
            logging.exception(f"Task '{task_name}' failed")
            await notify_admin_error(e, f"Task '{task_name}' failed (all models)")
        try:
            await target.send(f"❌ Task '{task_name}' 無法完成，已通知管理員。")
        except Exception:
            pass


def parse_cron(expr: str) -> dict[str, Any]:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron: {expr}")
    minute, hour, day, month, dow = parts
    kwargs = {"second": 0}
    if minute != "*": kwargs["minute"] = minute
    if hour != "*": kwargs["hour"] = hour
    if day != "*": kwargs["day"] = day
    if month != "*": kwargs["month"] = month
    if dow != "*": kwargs["day_of_week"] = dow
    return kwargs


def setup_scheduled_tasks() -> None:
    if not config.get("enable_scheduled_tasks", True):
        return
    legacy = config.get("scheduled_tasks", {})
    if isinstance(legacy, dict) and "enabled" in legacy and "cron" in legacy:
        if legacy.get("enabled"):
            try:
                scheduler.add_job(run_scheduled_task, "cron", id="email_check", replace_existing=True,
                                   args=["email_check", legacy], **parse_cron(legacy.get("cron", "0 9 * * *")))
                logging.info(f"Legacy scheduled task: {legacy.get('cron')}")
            except Exception as e:
                logging.error(f"Failed to setup legacy task: {e}")
        return

    tasks = load_scheduled_tasks(config)
    for name, tc in tasks.items():
        if not isinstance(tc, dict) or not tc.get("enabled", False):
            continue
        try:
            scheduler.add_job(run_scheduled_task, "cron", id=f"scheduled_task_{name}", replace_existing=True,
                               args=[name, tc], **parse_cron(tc.get("cron", "0 9 * * *")))
            logging.info(f"Scheduled task '{name}': {tc.get('cron')}")
        except Exception as e:
            logging.error(f"Failed to setup task '{name}': {e}")


async def main() -> None:
    await discord_bot.start(config["bot_token"])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
