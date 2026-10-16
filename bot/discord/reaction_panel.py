"""Private owner-only Discord UI for per-member reaction settings."""
from __future__ import annotations

import asyncio
import logging
import re

import discord
import emoji as emoji_lib

from bot.discord.self_reactions import DEFAULT_REACTIONS

logger = logging.getLogger(__name__)
CUSTOM_EMOJI = re.compile(r"<a?:[A-Za-z0-9_~]{2,32}:[0-9]+>|:[A-Za-z0-9_~]{2,32}:")


def parse_reactions(text: str, custom_emojis=()) -> list[str]:
    """Preserve Unicode sequences and validate this guild's custom emojis."""
    unicode_matches = {item["match_start"]: item for item in emoji_lib.emoji_list(text)}
    by_id = {str(item.id): item for item in custom_emojis}
    by_name = {item.name: item for item in custom_emojis}
    result = []
    position = 0
    while position < len(text):
        if text[position].isspace() or text[position] in ",;|":
            position += 1
            continue
        custom = CUSTOM_EMOJI.match(text, position)
        if custom:
            token = custom.group()
            candidate = by_id.get(token.rsplit(":", 1)[-1][:-1]) if token.startswith("<") else by_name.get(token[1:-1])
            if candidate is None or not candidate.available or not candidate.is_usable():
                raise ValueError("Özel emoji bu sunucuda bulunmuyor veya bot tarafından kullanılamıyor.")
            value = str(candidate)
            position = custom.end()
        elif position in unicode_matches:
            item = unicode_matches[position]
            value = item["emoji"]
            position = item["match_end"]
        elif "\U0001f1e6" <= text[position] <= "\U0001f1ff":
            # Discord also supports individual regional-indicator letters.
            value = text[position]
            position += 1
        else:
            raise ValueError("Yalnız emoji gir. Örnek: 😂 🔥 veya sunucunun özel emojisi için :emoji_adi:")
        if value not in result:
            result.append(value)
    if not 1 <= len(result) <= 8:
        raise ValueError("Bir ile sekiz arasında farklı emoji seçmelisin.")
    return result


class ReactionEmojiModal(discord.ui.Modal, title="Otomatik Tepki Emojileri"):
    def __init__(self, panel):
        super().__init__(timeout=300)
        self.panel = panel
        # Capture the target now: a selection change cannot redirect this edit.
        self.target_id = panel.selected_id
        self.target_name = panel.selected_name
        self.emojis_input = discord.ui.TextInput(
            label="Emojiler (en fazla 8)", placeholder="😂 🔥 veya :sunucu_emojisi:",
            default=" ".join(panel.draft), max_length=600,
        )
        self.add_item(self.emojis_input)

    async def interaction_check(self, interaction):
        return await self.panel.interaction_check(interaction)

    async def on_submit(self, interaction):
        if self.panel.is_finished():
            await interaction.response.send_message("Panelin süresi doldu. /tepkiler ile yeniden aç.", ephemeral=True)
            return
        try:
            draft = parse_reactions(str(self.emojis_input), self.panel.guild.emojis)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.panel.selected_id = self.target_id
        self.panel.selected_name = self.target_name
        self.panel.draft = draft
        self.panel.refresh_controls()
        await interaction.response.edit_message(
            embed=self.panel.build_embed("Emoji seçimi hazır. Uygulamak için Kaydet ve aç düğmesine bas."),
            view=self.panel,
        )


class ReactionPanel(discord.ui.View):
    def __init__(self, service, guild, owner_id):
        super().__init__(timeout=900)
        self.service = service
        self.guild = guild
        self.owner_id = owner_id
        self.selected_id = None
        self.selected_name = ""
        self.draft = []
        self.message = None
        self.refresh_controls()

    async def interaction_check(self, interaction):
        if (interaction.user.id != self.owner_id or interaction.user.id not in self.service.controller_user_ids
                or interaction.guild_id != self.guild.id):
            await interaction.response.send_message("Bu paneli yalnızca açan yetkili kişi kullanabilir.", ephemeral=True)
            return False
        return True

    def current_profile(self):
        return self.service.profiles.get(self.service.key(self.guild.id, self.selected_id)) if self.selected_id else None

    def refresh_controls(self):
        saved = self.current_profile()
        self.edit_emojis.disabled = self.selected_id is None
        self.save_profile.disabled = not self.draft or self.selected_id is None
        self.pause_profile.disabled = not (saved and saved["enabled"])
        self.remove_profile.disabled = saved is None

    def build_embed(self, notice=None):
        count = sum(1 for key, value in self.service.profiles.items()
                    if key.startswith(f"{self.guild.id}:") and value["enabled"])
        embed = discord.Embed(
            title="Otomatik Tepki Yönetimi",
            description="Kullanıcı seç → emojileri düzenle → Kaydet ve aç.\nBu panel yalnızca sana görünür.",
            color=0x5865F2,
        )
        if self.selected_id:
            saved = self.current_profile()
            state = "Açık" if saved and saved["enabled"] else "Kapalı"
            embed.add_field(name="Seçilen kullanıcı", value=discord.utils.escape_markdown(self.selected_name), inline=False)
            embed.add_field(name="Kayıtlı durum", value=state)
            embed.add_field(name="Emoji seçimi", value=" ".join(self.draft) or "Henüz emoji seçilmedi.", inline=False)
        if notice:
            embed.add_field(name="Bilgi", value=notice, inline=False)
        embed.set_footer(text=f"{count} kullanıcı için açık · Panel 15 dakika geçerli · Ayarlar kalıcıdır")
        return embed

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Bir kullanıcı seç veya adını ara", row=0)
    async def select_user(self, interaction, select):
        member = select.values[0]
        if member.bot:
            await interaction.response.send_message("Bot hesapları için otomatik tepki ayarlanamaz.", ephemeral=True)
            return
        self.selected_id = member.id
        self.selected_name = member.display_name
        saved = self.current_profile()
        self.draft = list(saved["emojis"]) if saved else (
            list(DEFAULT_REACTIONS) if member.id in self.service.target_user_ids else []
        )
        self.refresh_controls()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Emojileri düzenle", style=discord.ButtonStyle.secondary, row=1)
    async def edit_emojis(self, interaction, button):
        await interaction.response.send_modal(ReactionEmojiModal(self))

    @discord.ui.button(label="Kaydet ve aç", style=discord.ButtonStyle.success, row=1)
    async def save_profile(self, interaction, button):
        target_id = self.selected_id
        # Recheck custom-emoji availability when committing a previously edited draft.
        try:
            draft = parse_reactions(" ".join(self.draft), self.guild.emojis)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer()
        async with self.service.lock:
            await asyncio.to_thread(self.service.set_profile, self.guild.id, interaction.user.id, target_id, draft, True)
        self.refresh_controls()
        await interaction.edit_original_response(embed=self.build_embed("Kaydedildi. Bu kullanıcının yeni mesajlarına uygulanacak."), view=self)

    @discord.ui.button(label="Durdur", style=discord.ButtonStyle.secondary, row=1)
    async def pause_profile(self, interaction, button):
        target_id = self.selected_id
        await interaction.response.defer()
        async with self.service.lock:
            saved = self.service.profiles.get(self.service.key(self.guild.id, target_id))
            if saved:
                await asyncio.to_thread(self.service.set_profile, self.guild.id, interaction.user.id, target_id, saved["emojis"], False)
        self.refresh_controls()
        await interaction.edit_original_response(embed=self.build_embed("Otomatik tepkiler durduruldu; emoji ayarları saklandı."), view=self)

    @discord.ui.button(label="Sil", style=discord.ButtonStyle.danger, row=1)
    async def remove_profile(self, interaction, button):
        target_id = self.selected_id
        await interaction.response.defer()
        async with self.service.lock:
            await asyncio.to_thread(self.service.delete_profile, self.guild.id, interaction.user.id, target_id)
        if self.selected_id == target_id:
            self.draft = []
        self.refresh_controls()
        await interaction.edit_original_response(embed=self.build_embed("Bu kullanıcının tepki ayarı silindi."), view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(embed=self.build_embed("Panelin süresi doldu. /tepkiler ile yeniden açabilirsin."), view=self)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        logger.error("Reaction panel action failed: %s", type(error).__name__)
        if interaction.response.is_done():
            await interaction.followup.send("Ayar kaydedilemedi. Yeniden dene veya /tepkiler ile paneli aç.", ephemeral=True)
        else:
            await interaction.response.send_message("Ayar kaydedilemedi. Yeniden dene.", ephemeral=True)
