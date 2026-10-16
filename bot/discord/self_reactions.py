"""Account-controlled automatic reactions; no targeting of other accounts."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import discord
from discord import app_commands

logger = logging.getLogger(__name__)
DEFAULT_REACTIONS = ("🇬", "🇦", "🇾", "🍆")


class SelfReactions:
    def __init__(self, data_dir: str, guild_ids=()):
        self.path = Path(data_dir) / "self_reactions.json"
        self.guild_ids = {int(guild) for guild in guild_ids}
        self.profiles = {}
        if self.path.exists():
            self.profiles = json.loads(self.path.read_text(encoding="utf-8"))
        self.lock = asyncio.Lock()
        self.queue = asyncio.Queue(maxsize=100)
        self.worker = None

    @staticmethod
    def key(guild_id: int, user_id: int) -> str:
        return f"{guild_id}:{user_id}"

    def set_enabled(self, guild_id: int, user_id: int, enabled: bool):
        updated = dict(self.profiles)
        key = self.key(guild_id, user_id)
        if enabled:
            updated[key] = list(DEFAULT_REACTIONS)
        else:
            updated.pop(key, None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(updated, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.path)
        self.profiles = updated

    async def on_message(self, message):
        if message.guild is None or message.author.bot:
            return
        if self.guild_ids and message.guild.id not in self.guild_ids:
            return
        key = self.key(message.guild.id, message.author.id)
        if key not in self.profiles:
            return
        try:
            self.queue.put_nowait((key, message))
        except asyncio.QueueFull:
            logger.warning("Self reaction queue full; skipped one message")
            return
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._drain())

    async def _drain(self):
        while not self.queue.empty():
            key, message = self.queue.get_nowait()
            try:
                for emoji in tuple(self.profiles.get(key, ())):
                    if key not in self.profiles:
                        break
                    await message.add_reaction(emoji)
            except discord.HTTPException as exc:
                logger.warning("Self reactions unavailable: %s", type(exc).__name__)
            finally:
                self.queue.task_done()


def install_self_reactions(bot, config):
    memory = config.get("memory", {})
    service = SelfReactions(
        memory.get("data_dir", "/var/lib/discord-ai-memory"),
        memory.get("guild_ids", []),
    )
    bot.add_listener(service.on_message, "on_message")

    @bot.tree.command(name="tepkiler", description="Kendi mesajlarındaki otomatik tepki profilini aç veya kapat")
    @app_commands.guild_only()
    @app_commands.describe(acik="Aç: 🇬 🇦 🇾 🍆 tepkilerini kendi yeni mesajlarına ekler. Kapat: durdurur.")
    async def self_reactions(interaction: discord.Interaction, acik: bool):
        if interaction.guild is None:
            await interaction.response.send_message("Bu ayar yalnızca sunucuda kullanılabilir.", ephemeral=True)
            return
        if service.guild_ids and interaction.guild.id not in service.guild_ids:
            await interaction.response.send_message("Bu sunucuda tepki profilleri açık değil.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with service.lock:
            await asyncio.to_thread(service.set_enabled, interaction.guild.id, interaction.user.id, acik)
        text = (
            "Bu sunucuda kendi yeni mesajların için 🇬 🇦 🇾 🍆 tepkilerini açtın. "
            "Kapatmak için /tepkiler acik:False kullan."
            if acik else "Kendi mesajlarındaki otomatik tepkileri kapattın."
        )
        await interaction.followup.send(text, ephemeral=True)

    return service
