"""Persistent owner-managed, per-member automatic reaction profiles."""
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
    def __init__(self, data_dir: str, guild_ids=(), controller_user_id=None, target_user_ids=(), controller_user_ids=()):
        self.path = Path(data_dir) / "self_reactions.json"
        self.guild_ids = {int(guild) for guild in guild_ids}
        self.controller_user_id = int(controller_user_id) if controller_user_id else None
        self.controller_user_ids = {int(user_id) for user_id in controller_user_ids}
        if self.controller_user_id:
            self.controller_user_ids.add(self.controller_user_id)
        self.target_user_ids = {int(user_id) for user_id in target_user_ids}
        self.profiles = {}
        self.revision = 0
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") == 2:
                self.profiles = data["profiles"]
                self.revision = data.get("revision", 0)
            else:
                # Only migrate the former explicitly configured account group;
                # obsolete self-service entries must not become active again.
                self.profiles = {
                    key: {"emojis": emojis, "enabled": True, "revision": 0}
                    for key, emojis in data.items()
                    if key.rsplit(":", 1)[-1] in {str(uid) for uid in self.target_user_ids}
                }
        self.lock = asyncio.Lock()
        self.queue = asyncio.Queue(maxsize=100)
        self.worker = None

    @staticmethod
    def key(guild_id: int, user_id: int) -> str:
        return f"{guild_id}:{user_id}"

    def check_controller(self, guild_id: int, actor_id: int):
        if actor_id not in self.controller_user_ids:
            raise PermissionError("Only the configured controller can change reactions")
        if self.guild_ids and guild_id not in self.guild_ids:
            raise ValueError("Reactions are not configured for this guild")

    def _write(self, updated):
        revision = self.revision + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"version": 2, "revision": revision, "profiles": updated}, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.path)
        self.profiles = updated
        self.revision = revision

    def set_profile(self, guild_id: int, actor_id: int, user_id: int, emojis, enabled: bool):
        self.check_controller(guild_id, actor_id)
        if int(user_id) <= 0 or not 1 <= len(emojis) <= 8 or len(set(emojis)) != len(emojis):
            raise ValueError("Bir ile sekiz arasında farklı emoji seçmelisin.")
        updated = dict(self.profiles)
        updated[self.key(guild_id, user_id)] = {
            "emojis": list(emojis), "enabled": enabled, "revision": self.revision + 1,
        }
        self._write(updated)

    def delete_profile(self, guild_id: int, actor_id: int, user_id: int):
        self.check_controller(guild_id, actor_id)
        updated = dict(self.profiles)
        updated.pop(self.key(guild_id, user_id), None)
        self._write(updated)

    def set_enabled(self, guild_id: int, actor_id: int, enabled: bool):
        """Compatibility helper for the original configured account group."""
        self.check_controller(guild_id, actor_id)
        if not self.target_user_ids:
            raise ValueError("No reaction accounts configured")
        for user_id in self.target_user_ids:
            saved = self.profiles.get(self.key(guild_id, user_id), {})
            self.set_profile(guild_id, actor_id, user_id, saved.get("emojis", DEFAULT_REACTIONS), enabled)

    async def on_message(self, message):
        if message.guild is None or message.author.bot:
            return
        if self.guild_ids and message.guild.id not in self.guild_ids:
            return
        key = self.key(message.guild.id, message.author.id)
        profile = self.profiles.get(key)
        if not profile or not profile["enabled"]:
            return
        try:
            self.queue.put_nowait((key, profile["revision"], message))
        except asyncio.QueueFull:
            logger.warning("Self reaction queue full; skipped one message")
            return
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._drain())

    async def _drain(self):
        while not self.queue.empty():
            key, revision, message = self.queue.get_nowait()
            try:
                profile = self.profiles.get(key, {})
                for emoji in tuple(profile.get("emojis", ())):
                    current = self.profiles.get(key, {})
                    if not current.get("enabled") or current.get("revision") != revision:
                        break
                    await message.add_reaction(emoji)
            except discord.HTTPException as exc:
                logger.warning("Self reactions unavailable: %s", type(exc).__name__)
            finally:
                self.queue.task_done()


def install_self_reactions(bot, config):
    memory = config.get("memory", {})
    settings = config.get("automatic_reactions", {})
    service = SelfReactions(
        memory.get("data_dir", "/var/lib/discord-ai-memory"),
        memory.get("guild_ids", []),
        settings.get("controller_user_id"),
        settings.get("target_user_ids", []),
        settings.get("controller_user_ids", []),
    )
    bot.add_listener(service.on_message, "on_message")

    @bot.tree.command(name="tepkiler", description="Kullanıcıların otomatik emoji tepkilerini özel panelden yönet")
    @app_commands.guild_only()
    async def self_reactions(interaction: discord.Interaction):
        if interaction.user.id not in service.controller_user_ids:
            await interaction.response.send_message("Bu komut için yetkin yok.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("Bu ayar yalnızca sunucuda kullanılabilir.", ephemeral=True)
            return
        if service.guild_ids and interaction.guild.id not in service.guild_ids:
            await interaction.response.send_message("Bu sunucuda tepki profilleri açık değil.", ephemeral=True)
            return
        from bot.discord.reaction_panel import ReactionPanel
        panel = ReactionPanel(service, interaction.guild, interaction.user.id)
        await interaction.response.send_message(embed=panel.build_embed(), view=panel, ephemeral=True)
        panel.message = await interaction.original_response()

    return service
