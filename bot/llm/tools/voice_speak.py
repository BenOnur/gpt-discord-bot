"""
Discord Voice & Edge-TTS Speech tool ('Ses Kanalına Girip Konuşma / Racon Kesme').
Connects to the user's voice channel in Discord, speaks text using Turkish Edge-TTS (AhmetNeural), and leaves.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

import discord

from bot.llm.tools._types import ToolEntry
from bot.llm.tools.discord_ctx import get_current_discord_context
from bot.voice.edge_tts_service import synthesize_speech, DEFAULT_VOICE

logger = logging.getLogger(__name__)


async def _async_voice_speak(text: str, target_user: str | None, ctx: dict[str, Any]) -> str:
    """Async core of voice_speak running on Discord bot event loop."""
    guild = ctx.get("guild")
    author = ctx.get("author")
    bot = ctx.get("bot")

    if not guild or not bot:
        return "Discord ses bağlamı bulunamadı (sunucu veya bot aktif değil)."

    # 1. Determine target voice channel
    voice_channel = None

    # Check author's voice state (resolve as guild member to ensure accurate voice state)
    member = guild.get_member(author.id) if (guild and author) else None
    if member and member.voice and member.voice.channel:
        voice_channel = member.voice.channel
    elif author and hasattr(author, "voice") and author.voice and author.voice.channel:
        voice_channel = author.voice.channel

    # If target_user specified, search that user's voice state
    if not voice_channel and target_user:
        clean_target = target_user.strip().lstrip("@").lower()
        for member in guild.members:
            if clean_target in member.name.lower() or (member.nick and clean_target in member.nick.lower()):
                if member.voice and member.voice.channel:
                    voice_channel = member.voice.channel
                    break

    # If still not found, check any active voice channel with members
    if not voice_channel:
        for vc in guild.voice_channels:
            if len(vc.members) > 0:
                voice_channel = vc
                break

    if not voice_channel:
        return "Ses kanalında kimse bulunamadı. Konuşmamı duymak için önce bir ses kanalına girmelisin."

    # 2. Synthesize audio via Edge-TTS
    temp_path = None
    try:
        temp_path = await synthesize_speech(text, voice=DEFAULT_VOICE)
    except Exception as e:
        logger.error("Edge-TTS synthesis error in voice_speak: %s", e)
        return f"Ses sentezlenirken hata oluştu: {e}"

    # 3. Connect to voice channel and play
    vc = None
    try:
        if guild.voice_client and guild.voice_client.is_connected():
            vc = guild.voice_client
            if vc.channel != voice_channel:
                await vc.move_to(voice_channel)
        else:
            vc = await voice_channel.connect(timeout=30.0, reconnect=True, self_deaf=True)

        # Play audio using FFmpeg
        audio_source = discord.FFmpegPCMAudio(temp_path)
        vc.play(audio_source)

        # Wait until playback completes (max 45 seconds safety timeout)
        wait_seconds = 0.0
        while vc.is_playing() and wait_seconds < 45.0:
            await asyncio.sleep(0.3)
            wait_seconds += 0.3

        # Clean disconnect
        if vc.is_connected():
            await vc.disconnect(force=True)

        return f"Ses kanalına ({voice_channel.name}) girildi ve şu söz sesli olarak söylendi: {text}"
    except Exception as e:
        logger.error("Voice playback error in voice_speak: %s", e)
        if vc and vc.is_connected():
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
        return f"Ses kanalında konuşurken hata oluştu: {e}"
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


def voice_speak(text: str, target_user: str | None = None) -> str:
    """
    Synchronous entry point called by the tool executor.
    Dispatches to bot event loop when running in thread.
    """
    clean_text = text.strip()
    if not clean_text:
        return "Söylenecek bir metin belirtilmedi."

    ctx = get_current_discord_context()
    bot = ctx.get("bot")

    if not bot or not hasattr(bot, "loop") or not bot.loop.is_running():
        # Standalone or test environment
        return f"[Test/Simülasyon] Ses kanalına bağlanılamadı (canlı Discord bot döngüsü yok): '{clean_text}'"

    try:
        future = asyncio.run_coroutine_threadsafe(
            _async_voice_speak(clean_text, target_user, ctx),
            bot.loop,
        )
        return future.result(timeout=60.0)
    except Exception as e:
        logger.error("voice_speak threadsafe error: %s", e)
        return f"Sesli konuşma yürütülürken hata oluştu: {e}"


VOICE_SPEAK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "voice_speak",
        "description": "Join the user's voice channel in Discord, speak the given text aloud with Turkish Edge-TTS (Ahmet voice), and disconnect.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The exact sentence or punchline to speak aloud in the voice channel",
                },
                "target_user": {
                    "type": "string",
                    "description": "Optional username to target if the command is addressing a specific user in voice",
                },
            },
            "required": ["text"],
        },
    },
}

TOOL_NAME = "voice_speak"
TOOL_ENTRY = ToolEntry(
    schema=VOICE_SPEAK_SCHEMA,
    fn=voice_speak,
)
