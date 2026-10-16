"""
Discord Server Message Trivia tool ('Bunu Kim Söyledi?').
Starts an interactive 4-choice trivia game in the channel with buttons.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import discord

from bot.llm.tools._types import ToolEntry
from bot.llm.tools.discord_ctx import get_current_discord_context
from bot.trivia.game import get_random_trivia, get_trivia_leaderboard, TriviaView

logger = logging.getLogger(__name__)


async def _async_start_trivia(query: Optional[str], show_leaderboard: bool, ctx: dict[str, Any]) -> str:
    bot = ctx.get("bot")
    channel = ctx.get("channel")
    config = ctx.get("config", {})

    if not channel:
        return "Discord kanal bağlamı bulunamadı. Trivia başlatılamadı."

    data_dir = config.get("memory", {}).get("data_dir", "/var/lib/discord-ai-memory")

    if show_leaderboard:
        board = get_trivia_leaderboard(data_dir=data_dir, limit=10)
        if not board:
            return "Henüz kayıtlı bir trivia skoru bulunmuyor. Bir oyun başlatmak için '/kimdedi oyna' yaz!"
        lines = ["🏆 **BUNU KİM SÖYLEDİ? - LİDERLİK TABLOSU**\n"]
        medals = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 11)]
        for idx, row in enumerate(board):
            medal = medals[idx] if idx < len(medals) else f"{idx+1}."
            lines.append(f"{medal} **{row['user_name']}**: {row['points']} Puan ({row['wins']} Doğru)")
        
        embed = discord.Embed(
            title="🏆 Trivia Liderlik Tablosu",
            description="\n".join(lines),
            color=0xFEE75C,
        )
        await channel.send(embed=embed)
        return "Trivia liderlik tablosu kanala gönderildi."

    trivia_data = await asyncio.to_thread(get_random_trivia, data_dir=data_dir, search_query=query)
    if not trivia_data:
        return "Sunucu arşivinde trivia için uygun mesaj bulunamadı veya arama kriteriyle eşleşen mesaj yok."

    view = TriviaView(trivia_data=trivia_data, data_dir=data_dir)
    embed = view.build_embed()

    msg = await channel.send(embed=embed, view=view)
    view.message = msg

    if isinstance(ctx, dict):
        ctx["suppress_chat_reply"] = True
        ctx["suppress_reaction"] = "❓"

    return f"'{trivia_data['correct_author_name']}' sözü için 'Bunu Kim Söyledi?' oyunu kanalda başlatıldı."


def server_trivia(query: Optional[str] = None, show_leaderboard: bool = False) -> str:
    """
    Synchronous entry point called by LLM tool executor.
    Dispatches to bot event loop when running in thread.
    """
    ctx = get_current_discord_context()
    bot = ctx.get("bot")

    if not bot or not hasattr(bot, "loop") or not bot.loop.is_running():
        return f"[Test/Simülasyon] Server trivia başlatıldı (query: {query}, leaderboard: {show_leaderboard})"

    try:
        future = asyncio.run_coroutine_threadsafe(
            _async_start_trivia(query, show_leaderboard, ctx),
            bot.loop,
        )
        return future.result(timeout=15.0)
    except Exception as e:
        logger.error("server_trivia threadsafe error: %s", e)
        return f"Trivia başlatılırken hata oluştu: {e}"


SERVER_TRIVIA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "server_trivia",
        "description": "Start an interactive 4-choice 'Bunu Kim Söyledi?' (Who said this?) trivia quiz game in the Discord channel using quotes from the server's message archive. Supports button clicks, timeout, scoring and leaderboard.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional search term to filter quoted messages (e.g. 'cs2', 'para', 'yemek')",
                },
                "show_leaderboard": {
                    "type": "boolean",
                    "description": "Set to true to display the trivia leaderboard instead of starting a new question",
                },
            },
        },
    },
}

TOOL_NAME = "server_trivia"
TOOL_ENTRY = ToolEntry(
    schema=SERVER_TRIVIA_SCHEMA,
    fn=server_trivia,
)
