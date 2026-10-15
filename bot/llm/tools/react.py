"""
Discord emoji reaction tool.
Allows the bot to react to the current message with an emoji (e.g. 💀, 🤡, 🗿, 🔥, 👑).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from bot.llm.tools._types import ToolEntry
from bot.llm.tools.discord_ctx import get_current_discord_context


def react_to_message(emoji: str) -> str:
    """Add a Discord emoji reaction to the current message."""
    clean_emoji = emoji.strip()
    if not clean_emoji:
        return "Tepki için geçerli bir emoji belirtilmedi."

    ctx = get_current_discord_context()
    message = ctx.get("message")
    bot = ctx.get("bot")

    if not message or not bot:
        return "Discord mesaj bağlamı bulunamadı. Tepki eklenemedi."

    try:
        coro = message.add_reaction(clean_emoji)
        if bot.loop and bot.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, bot.loop)
            future.result(timeout=4)
            return f"Mesaja '{clean_emoji}' emojisiyle tepki verildi."
        else:
            return f"Bot döngüsü aktif değil, reaksiyon eklenemedi."
    except Exception as e:
        logging.warning("react_to_message failed for emoji '%s': %s", clean_emoji, e)
        return f"'{clean_emoji}' emojisi eklenirken hata oluştu: {e}"


REACT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "react_to_message",
        "description": "Add an emoji reaction (e.g. '💀', '🤡', '🗿', '🔥', '👑', '👍') to the user's message. Can be called alongside or instead of a text response when a reaction fits best.",
        "parameters": {
            "type": "object",
            "properties": {
                "emoji": {
                    "type": "string",
                    "description": "The standard Unicode emoji to react with (e.g. '💀', '🤡', '🔥', '👑', '🗿', '❤️')",
                }
            },
            "required": ["emoji"],
        },
    },
}

TOOL_NAME = "react_to_message"
TOOL_ENTRY = ToolEntry(
    schema=REACT_SCHEMA,
    fn=react_to_message,
)
