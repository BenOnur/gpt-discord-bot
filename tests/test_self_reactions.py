import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.discord.self_reactions import DEFAULT_REACTIONS, SelfReactions


class SelfReactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = SelfReactions(self.temp.name, [1], controller_user_id=99, target_user_ids=[2, 4])

    def message(self, user_id=2, guild_id=1, bot=False):
        return SimpleNamespace(
            guild=SimpleNamespace(id=guild_id) if guild_id else None,
            author=SimpleNamespace(id=user_id, bot=bot), add_reaction=AsyncMock(),
        )

    async def test_only_enabled_account_in_selected_guild_gets_reactions(self):
        self.service.set_enabled(1, 99, True)
        enabled = self.message()
        others = [self.message(user_id=3), self.message(guild_id=4), self.message(guild_id=None), self.message(bot=True)]
        for message in [enabled] + others:
            await self.service.on_message(message)
        await self.service.worker
        self.assertEqual([call.args[0] for call in enabled.add_reaction.await_args_list], list(DEFAULT_REACTIONS))
        for other in others:
            other.add_reaction.assert_not_awaited()

    async def test_settings_survive_restart_and_disable_stops_queued_reactions(self):
        self.service.set_enabled(1, 99, True)
        reloaded = SelfReactions(self.temp.name, [1], 99, [2, 4])
        self.assertIn("1:2", reloaded.profiles)
        message = self.message()
        await self.service.on_message(message)
        self.service.set_enabled(1, 99, False)
        await self.service.worker
        message.add_reaction.assert_not_awaited()
        self.assertNotIn("1:2", SelfReactions(self.temp.name).profiles)

    async def test_only_controller_can_toggle_all_configured_accounts(self):
        for actor_id in (2, 3, 4):
            with self.assertRaises(PermissionError):
                self.service.set_enabled(1, actor_id, True)
        self.assertEqual(self.service.profiles, {})
        self.service.set_enabled(1, 99, True)
        self.assertEqual(set(self.service.profiles), {"1:2", "1:4"})
        with self.assertRaises(PermissionError):
            self.service.set_enabled(1, 2, False)
        self.service.set_enabled(1, 99, False)
        self.assertEqual(self.service.profiles, {})

    async def test_legacy_self_profile_for_other_account_is_ignored_and_removed(self):
        self.service.profiles["1:3"] = list(DEFAULT_REACTIONS)
        other = self.message(user_id=3)
        await self.service.on_message(other)
        other.add_reaction.assert_not_awaited()
        self.assertIsNone(self.service.worker)
        self.service.set_enabled(1, 99, True)
        self.assertNotIn("1:3", self.service.profiles)
