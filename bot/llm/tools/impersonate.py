"""
AI Personality Impersonation Tool.
Allows the LLM or chat to dynamically impersonate/clone a server member
by mining their historical messages and stylistic mannerisms from SQLite.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import discord

from bot.llm.tools._types import ToolEntry
from bot.llm.tools.discord_ctx import get_current_discord_context
from bot.clone.simulator import get_user_training_data, build_impersonation_prompt

logger = logging.getLogger(__name__)


def impersonate_user(user_name_or_id: str, topic_or_question: str) -> str:
    """
    Synchronous entry point called by LLM tool executor.
    Returns background training data, mannerisms, and instructions to impersonate user.
    """
    ctx = get_current_discord_context()
    guild = ctx.get("guild") if isinstance(ctx, dict) else None
    config = ctx.get("config", {}) if isinstance(ctx, dict) else {}
    data_dir = config.get("memory", {}).get("data_dir", "/var/lib/discord-ai-memory")

    clean_q = user_name_or_id.strip().lstrip("@")
    target_id: int | None = None
    target_name: str = clean_q

    if clean_q.isdigit():
        target_id = int(clean_q)
    elif guild:
        for m in guild.members:
            if clean_q.lower() in (m.name.lower(), m.display_name.lower()):
                target_id = m.id
                target_name = m.name
                break

    sample_msgs, top_words, dossier = get_user_training_data(
        user_id=target_id or 0,
        user_name=target_name,
        data_dir=data_dir,
    )

    if not sample_msgs and not dossier:
        return f"'{user_name_or_id}' kullanıcısına ait sunucu arşivinde yeterli mesaj geçmişi bulunamadı."

    return (
        f"[KLON MODU AKTİF]: Şimdi '{target_name}' kullanıcısını taklit edeceksin!\n"
        f"Sık kullandığı kelimeler: {', '.join(top_words) if top_words else 'Belirgin yok'}\n"
        f"Karakteristik mesaj örnekleri:\n" + "\n".join(f"- {m}" for m in sample_msgs[:20]) +
        f"\n\nSoru/Konu: {topic_or_question}\n"
        f"Şimdi bu konuya TAMAMEN '{target_name}' gibi, onun ağzıyla ve imlasıyla cevap ver!"
    )


IMPERSONATE_USER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "impersonate_user",
        "description": "Impersonate and speak as an AI clone of a specific Discord server member by loading their speech style, slang, and opinions from historical archives.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_name_or_id": {
                    "type": "string",
                    "description": "Username, mention, or ID of the server member to impersonate (e.g. 'Apro', 'Sezin', '19', 'Onur')",
                },
                "topic_or_question": {
                    "type": "string",
                    "description": "The topic, question, or context the clone should respond to",
                },
            },
            "required": ["user_name_or_id", "topic_or_question"],
        },
    },
}

TOOL_NAME = "impersonate_user"
TOOL_ENTRY = ToolEntry(
    schema=IMPERSONATE_USER_SCHEMA,
    handler=impersonate_user,
    requires_context=True,
)
