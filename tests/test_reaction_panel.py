import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

from bot.discord.reaction_panel import ReactionPanel, ReactionEmojiModal, parse_reactions
from bot.discord.self_reactions import SelfReactions, install_self_reactions


class CustomEmoji:
    id = 123456789012345678
    name = "dance"
    available = True

    def is_usable(self):
        return True

    def __str__(self):
        return f"<a:{self.name}:{self.id}>"


class ReactionEmojiTests(unittest.TestCase):
    def test_unicode_sequences_letters_and_deduplication(self):
        self.assertEqual(parse_reactions("😂🔥 😂 👨‍👩‍👧 🇹🇷 1️⃣ 🇬 🇦 🇾"),
                         ["😂", "🔥", "👨‍👩‍👧", "🇹🇷", "1️⃣", "🇬", "🇦", "🇾"])

    def test_guild_custom_emojis_resolve_by_id_and_name(self):
        custom = CustomEmoji()
        self.assertEqual(parse_reactions(f":dance: {custom}", [custom]), [str(custom)])
        custom.available = False
        with self.assertRaises(ValueError):
            parse_reactions(str(custom), [custom])

    def test_invalid_text_unknown_custom_and_count_rejected(self):
        for text in ("hello 😂", "", ":unknown:", "😀😂🔥⭐🍆🥳🚀💚💙"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_reactions(text)


class ReactionPanelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = SelfReactions(self.temp.name, [1], 99, [2])
        self.guild = SimpleNamespace(id=1, emojis=[])
        self.panel = ReactionPanel(self.service, self.guild, 99)
        self.addCleanup(self.panel.stop)

    def interaction(self, user_id=99):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id), guild=self.guild, guild_id=1,
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def test_only_owner_can_use_panel_or_modal(self):
        stranger = self.interaction(22)
        self.assertFalse(await self.panel.interaction_check(stranger))
        stranger.response.send_message.assert_awaited_once()
        self.assertTrue(await self.panel.interaction_check(self.interaction()))
        modal = ReactionEmojiModal(self.panel)
        self.addCleanup(modal.stop)
        self.assertFalse(await modal.interaction_check(self.interaction(22)))

    async def test_buttons_save_pause_and_remove_only_selected_user(self):
        self.panel.selected_id = 7
        self.panel.selected_name = "Selected member"
        self.panel.draft = ["😂", "🔥"]
        self.panel.refresh_controls()
        await self.panel.save_profile.callback(self.interaction())
        self.assertTrue(self.service.profiles["1:7"]["enabled"])
        await self.panel.pause_profile.callback(self.interaction())
        self.assertFalse(self.service.profiles["1:7"]["enabled"])
        self.assertEqual(self.service.profiles["1:7"]["emojis"], ["😂", "🔥"])
        await self.panel.remove_profile.callback(self.interaction())
        self.assertNotIn("1:7", self.service.profiles)

    async def test_additional_managers_have_individual_panels_and_storage_access(self):
        service = SelfReactions(self.temp.name, [1], 99, [2], [100, 101])
        self.assertEqual(service.controller_user_ids, {99, 100, 101})
        for actor in (99, 100, 101):
            service.set_profile(1, actor, 7, ["🔥"], True)
            service.delete_profile(1, actor, 7)
        with self.assertRaises(PermissionError):
            service.set_profile(1, 102, 7, ["🔥"], True)
        with self.assertRaises(ValueError):
            service.set_profile(2, 100, 7, ["🔥"], True)
        panel = ReactionPanel(service, self.guild, 100)
        self.addCleanup(panel.stop)
        self.assertTrue(await panel.interaction_check(self.interaction(100)))
        self.assertFalse(await panel.interaction_check(self.interaction(99)))
        modal = ReactionEmojiModal(panel)
        self.addCleanup(modal.stop)
        self.assertFalse(await modal.interaction_check(self.interaction(101)))

    async def test_command_opens_ephemeral_panel_and_rejects_other_admins(self):
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        self.addAsyncCleanup(bot.close)
        install_self_reactions(bot, {"memory": {"data_dir": self.temp.name, "guild_ids": [1]},
                                     "automatic_reactions": {"controller_user_id": 99, "controller_user_ids": [100, 101]}})
        command = bot.tree.get_command("tepkiler")
        self.assertIsNone(command.default_permissions)
        for actor in (100, 101):
            manager = self.interaction(actor)
            manager.original_response = AsyncMock()
            await command.callback(manager)
            self.assertTrue(manager.response.send_message.await_args.kwargs["ephemeral"])
            manager.response.send_message.await_args.kwargs["view"].stop()
        owner = self.interaction()
        owner.original_response = AsyncMock()
        await command.callback(owner)
        self.assertTrue(owner.response.send_message.await_args.kwargs["ephemeral"])
        owner.response.send_message.await_args.kwargs["view"].stop()
        stranger = self.interaction(22)
        await command.callback(stranger)
        self.assertNotIn("view", stranger.response.send_message.await_args.kwargs)

    async def test_modal_captures_target_and_timeout_disables_controls(self):
        self.panel.selected_id = 7
        modal = ReactionEmojiModal(self.panel)
        self.addCleanup(modal.stop)
        self.panel.selected_id = 8
        self.assertEqual(modal.target_id, 7)
        self.panel.message = SimpleNamespace(edit=AsyncMock())
        await self.panel.on_timeout()
        self.assertTrue(all(item.disabled for item in self.panel.children))
