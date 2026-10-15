"""
Discord Equicord Quoter Quote Tool ('generate_quote').
Creates and sends a 1200x600 Equicord Quoter style quote card PNG image to the Discord channel.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Any, Optional

import discord

from bot.llm.tools._types import ToolEntry
from bot.llm.tools.discord_ctx import get_current_discord_context
from bot.quote.renderer import render_quote_card

logger = logging.getLogger(__name__)


async def _async_generate_quote(text: str | None, author: str | None, ctx: dict[str, Any]) -> str:
    bot = ctx.get("bot")
    message = ctx.get("message")
    channel = ctx.get("channel")

    if not message or not channel:
        return "Discord mesaj bağlamı bulunamadı. Quote kartı oluşturulamadı."

    target_text = text
    target_author = author
    avatar_bytes = None
    target_msg = None

    # 1. If user replied to a message, prioritize the replied message
    if message.reference and message.reference.message_id:
        try:
            ref = message.reference.resolved
            if isinstance(ref, discord.Message):
                target_msg = ref
            else:
                target_msg = await channel.fetch_message(message.reference.message_id)
        except Exception as e:
            logger.warning("Could not fetch referenced message for quote: %s", e)

    if target_msg:
        # Use replied message's content and author unless overridden
        if not target_text:
            target_text = target_msg.clean_content or target_msg.content
        if not target_author:
            target_author = target_msg.author.display_name or target_msg.author.name
        try:
            avatar_bytes = await target_msg.author.display_avatar.with_format("png").with_size(512).read()
        except Exception as e:
            logger.warning("Could not fetch avatar for quote author: %s", e)
    else:
        # Standalone invocation without reply
        if not target_text:
            # Fallback to cleaning the user's own message
            target_text = message.clean_content or message.content
        if not target_author:
            target_author = message.author.display_name or message.author.name
        try:
            avatar_bytes = await message.author.display_avatar.with_format("png").with_size(512).read()
        except Exception as e:
            logger.warning("Could not fetch avatar for author: %s", e)

    if not target_text or not target_text.strip():
        return "Alıntı yapılacak geçerli bir metin bulunamadı."

    try:
        # Generate PNG in background thread
        png_bytes = await asyncio.to_thread(
            render_quote_card,
            target_text,
            target_author or "Anonim",
            avatar_bytes,
        )

        file = discord.File(
            fp=io.BytesIO(png_bytes),
            filename=f"quote_{target_msg.id if target_msg else message.id}.png",
        )

        if target_msg:
            # Reply directly to the quoted message
            await target_msg.reply(file=file, mention_author=False)
        else:
            await message.reply(file=file, mention_author=False)

        if isinstance(ctx, dict):
            ctx["suppress_chat_reply"] = True
            ctx["suppress_reaction"] = "🖼️"

        return f"'{target_author}' için alıntı kartı oluşturulup kanala gönderildi."
    except Exception as e:
        logger.error("Error generating quote card: %s", e, exc_info=True)
        return f"Quote kartı oluşturulurken hata oluştu: {e}"


def generate_quote(text: str | None = None, author: str | None = None) -> str:
    """
    Synchronous entry point called by LLM tool executor.
    Dispatches to bot event loop when running in thread.
    """
    ctx = get_current_discord_context()
    bot = ctx.get("bot")

    if not bot or not hasattr(bot, "loop") or not bot.loop.is_running():
        return f"[Test/Simülasyon] Quote kartı üretildi: '{text or 'örnek'}' - {author or 'Anonim'}"

    try:
        future = asyncio.run_coroutine_threadsafe(
            _async_generate_quote(text, author, ctx),
            bot.loop,
        )
        return future.result(timeout=25.0)
    except Exception as e:
        logger.error("generate_quote threadsafe error: %s", e)
        return f"Quote kartı oluşturulurken hata oluştu: {e}"


GENERATE_QUOTE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "generate_quote",
        "description": "Generate an Equicord Quoter style quote card PNG image (1200x600 with author avatar gradient fade and typography) and send it to Discord. When the user replies to a message and asks for a quote, this captures the replied message.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Optional text to quote. If omitted, the replied-to message content is automatically used.",
                },
                "author": {
                    "type": "string",
                    "description": "Optional author name. If omitted, the replied-to message author is automatically used.",
                },
            },
        },
    },
}

TOOL_NAME = "generate_quote"
TOOL_ENTRY = ToolEntry(
    schema=GENERATE_QUOTE_SCHEMA,
    fn=generate_quote,
)
