"""
Local IPC Server for Discord AI Bot (Port 8799).
Allows authorized local services (e.g. Telegram Backup Bot Daemon) to:
- Send messages to any text channel as the bot (/say)
- Join voice channels, speak text in male neural voice, and leave (/tts)
- Query server channels and active voice members
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import discord

from bot.voice.edge_tts_service import synthesize_speech, DEFAULT_VOICE

logger = logging.getLogger("ipc_server")

IPC_HOST = "127.0.0.1"
IPC_PORT = 8799


async def _handle_say(discord_bot: discord.Client, req: dict[str, Any]) -> dict[str, Any]:
    guild = discord_bot.guilds[0] if discord_bot.guilds else None
    if not guild:
        return {"ok": False, "error": "Bot sunucuya bağlı değil"}

    target_name = req.get("channel", "çet").strip().lstrip("#").lower()
    text = req.get("text", "").strip()
    if not text:
        return {"ok": False, "error": "Boş mesaj gönderilemez"}

    target_channel = None
    for ch in guild.text_channels:
        if str(ch.id) == target_name or target_name in ch.name.lower():
            target_channel = ch
            break

    # Default to first text channel if not matched
    if not target_channel and guild.text_channels:
        target_channel = guild.text_channels[0]

    if not target_channel:
        return {"ok": False, "error": f"Metin kanalı bulunamadı: {target_name}"}

    msg = await target_channel.send(text)
    logger.info("IPC SAY sent to #%s: %s", target_channel.name, text)
    return {"ok": True, "channel": target_channel.name, "message_id": msg.id}


async def _handle_tts(discord_bot: discord.Client, req: dict[str, Any]) -> dict[str, Any]:
    guild = discord_bot.guilds[0] if discord_bot.guilds else None
    if not guild:
        return {"ok": False, "error": "Bot sunucuya bağlı değil"}

    text = req.get("text", "").strip()
    if not text:
        return {"ok": False, "error": "Söylenecek metin boş"}

    channel_name = req.get("channel", "").strip().lstrip("#").lower()
    voice_channel = None

    if channel_name:
        for vc in guild.voice_channels:
            if str(vc.id) == channel_name or channel_name in vc.name.lower():
                voice_channel = vc
                break

    # If no channel specified or not found, find channel with active human members
    if not voice_channel:
        for vc in guild.voice_channels:
            if any(not m.bot for m in vc.members):
                voice_channel = vc
                break

    # Fallback to any voice channel
    if not voice_channel and guild.voice_channels:
        voice_channel = guild.voice_channels[0]

    if not voice_channel:
        return {"ok": False, "error": "Ses kanalı bulunamadı"}

    logger.info("IPC TTS speaking in #%s: %s", voice_channel.name, text[:50])

    # Synthesize audio (guaranteed male voice)
    audio_path = None
    try:
        audio_path = await synthesize_speech(text, voice=DEFAULT_VOICE)
    except Exception as e:
        logger.error("IPC TTS synthesis failed: %s", e)
        return {"ok": False, "error": f"Ses sentezleme hatası: {e}"}

    vc_client = None
    try:
        if guild.voice_client and guild.voice_client.is_connected():
            vc_client = guild.voice_client
            if vc_client.channel != voice_channel:
                await vc_client.move_to(voice_channel)
        else:
            vc_client = await voice_channel.connect(timeout=25.0, reconnect=True, self_deaf=True)

        audio_source = discord.FFmpegPCMAudio(audio_path)
        vc_client.play(audio_source)

        wait_sec = 0.0
        while vc_client.is_playing() and wait_sec < 45.0:
            await asyncio.sleep(0.3)
            wait_sec += 0.3

        if vc_client.is_connected():
            await vc_client.disconnect(force=True)

        return {"ok": True, "channel": voice_channel.name, "text": text}
    except Exception as e:
        logger.error("IPC TTS playback error: %s", e)
        if vc_client and vc_client.is_connected():
            try:
                await vc_client.disconnect(force=True)
            except Exception:
                pass
        return {"ok": False, "error": f"Ses çalma hatası: {e}"}
    finally:
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except Exception:
                pass


async def _handle_channels(discord_bot: discord.Client) -> dict[str, Any]:
    guild = discord_bot.guilds[0] if discord_bot.guilds else None
    if not guild:
        return {"ok": False, "error": "Bot sunucuya bağlı değil"}

    text_chs = [{"id": c.id, "name": c.name} for c in guild.text_channels]
    voice_chs = [
        {
            "id": c.id,
            "name": c.name,
            "member_count": len(c.members),
            "members": [m.display_name for m in c.members if not m.bot],
        }
        for c in guild.voice_channels
    ]
    return {"ok": True, "guild": guild.name, "text_channels": text_chs, "voice_channels": voice_chs}


async def start_ipc_server(discord_bot: discord.Client) -> asyncio.Server:
    """Start local TCP IPC server on 127.0.0.1:8799."""
    async def _client_connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                return

            req = json.loads(line.decode("utf-8").strip())
            action = req.get("action", "")

            if action == "say":
                resp = await _handle_say(discord_bot, req)
            elif action == "tts":
                resp = await _handle_tts(discord_bot, req)
            elif action == "channels":
                resp = await _handle_channels(discord_bot)
            elif action == "ping":
                resp = {"ok": True, "status": "online", "user": str(discord_bot.user)}
            else:
                resp = {"ok": False, "error": f"Bilinmeyen eylem: {action}"}

            writer.write(json.dumps(resp).encode("utf-8") + b"\n")
            await writer.drain()
        except Exception as e:
            logger.error("IPC client error: %s", e)
            try:
                writer.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8") + b"\n")
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(_client_connected, IPC_HOST, IPC_PORT)
    logger.info("⚡ IPC server listening on http://%s:%d", IPC_HOST, IPC_PORT)
    return server
