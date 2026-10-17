import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from bot.llm.tools.member_info import PresenceStore, member_info_request
from bot.llm.tools.registry import build_tool_registry


class PresenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "presence.sqlite3"
        self.store = PresenceStore(str(self.db_path), retention_days=30, max_records=100)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_records_only_activity_transitions_and_closes_old_session(self):
        start = datetime(2026, 10, 16, 9, tzinfo=timezone.utc)
        self.store.update(1, 2, [SimpleNamespace(type=0, name="Game A", details="private details")], start)
        self.store.update(1, 2, [SimpleNamespace(type=0, name="Game A", details="changed details")], start + timedelta(minutes=5))
        self.store.update(1, 2, [SimpleNamespace(type=0, name="Game B")], start + timedelta(minutes=10))

        rows = self.store.history(1, 2)
        self.assertEqual([row["activity_name"] for row in rows], ["Game B", "Game A"])
        self.assertEqual(rows[0]["ended_at"], None)
        self.assertEqual(rows[1]["ended_at"], (start + timedelta(minutes=10)).isoformat())

        import sqlite3
        with sqlite3.connect(self.db_path) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(activity_sessions)")}
        self.assertEqual(columns, {"id", "guild_id", "user_id", "activity_type", "activity_name", "started_at", "ended_at"})

    def test_identical_activity_names_with_different_type_are_distinct(self):
        now = datetime(2026, 10, 16, tzinfo=timezone.utc)
        self.store.update(1, 2, [SimpleNamespace(type=0, name="A")], now)
        self.store.update(1, 2, [SimpleNamespace(type=2, name="A")], now + timedelta(minutes=1))
        rows = self.store.history(1, 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["activity_type"] for row in rows}, {0, 2})

    def test_prune_enforces_retention_and_global_row_cap(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index in range(105):
            self.store.update(1, 2, [SimpleNamespace(type=0, name=f"Game {index}")], start + timedelta(days=index))
        self.store.prune((start + timedelta(days=104)).isoformat())
        with self.store._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM activity_sessions").fetchone()[0]
        self.assertLessEqual(count, 100)
        self.assertTrue(all("Game" in row["activity_name"] for row in self.store.history(1, 2)))

        recent = datetime(2026, 10, 16, tzinfo=timezone.utc)
        for user_id in range(200, 310):
            self.store.update(1, user_id, [SimpleNamespace(type=0, name="Current")], recent)
        self.store.prune(recent.isoformat())
        with self.store._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM activity_sessions").fetchone()[0]
        self.assertLessEqual(count, 100)

    def test_tracking_start_is_persistent(self):
        started = self.store.tracking_started_at()
        self.assertTrue(started)
        reopened = PresenceStore(str(self.db_path))
        self.assertEqual(reopened.tracking_started_at(), started)

    def test_natural_language_queries_are_classified(self):
        self.assertEqual(member_info_request("şu an dinlediğim şarkı hakkında araştırma yap"), "music_research")
        self.assertEqual(member_info_request("Onur ne dinliyor"), "current_music")
        self.assertEqual(member_info_request("@bot Onur'un ne zaman sunucuya girdiğini söylesene"), "joined_at")
        self.assertEqual(member_info_request("Onur en son hangi oyunu oynamış?"), "last_game")
        self.assertEqual(member_info_request("Onur şu an ne oynuyor?"), "current_activity")
        self.assertEqual(member_info_request("Onur'un oyun geçmişi ne?"), "game_history")
        self.assertEqual(member_info_request("sezin en son ne oynamış listelesene"), "game_history")
        self.assertEqual(member_info_request("sezin en son ne oynamış"), "last_game")
        self.assertIsNone(member_info_request("Onur bugün nasıldı?"))

    def test_member_info_tool_is_auto_discovered(self):
        self.assertIn("get_member_info", build_tool_registry())

    def test_member_tool_is_available_without_exact_keyword(self):
        from bot.llm.tool_selection import select_tools
        self.assertIn("get_member_info", select_tools("sezin en son ne oynamış listelesene", ["get_member_info"]))
        self.assertIn("get_member_info", select_tools("sezin neler yapmış", ["get_member_info"]))

    def test_game_filter_precedes_limit_and_alias_resolves_named_person(self):
        import asyncio
        from bot.llm.tools.member_info import _async_get_member_info, _resolve_member
        start = datetime.now(timezone.utc)-timedelta(hours=1)
        self.store.update(1, 2, [SimpleNamespace(type=0, name="Game A")], start)
        for index in range(12):
            self.store.update(1, 2, [SimpleNamespace(type=2, name=f"Song {index}")], start+timedelta(minutes=index+1))
        self.assertEqual(self.store.history(1, 2, 8, activity_type=0)[0]["activity_name"], "Game A")
        member = SimpleNamespace(id=2,name="different_username",display_name="Nickname",joined_at=None,activities=[])
        guild = SimpleNamespace(id=1,members=[member],get_member=lambda uid: member if uid==2 else None)
        ctx = {"guild":guild,"author":SimpleNamespace(id=3),"message":SimpleNamespace(mentions=[], content="sezin en son ne oynamış listelesene"),
               "config":{"member_aliases":{"sezin":2}},"presence_tracker":SimpleNamespace(store=self.store,tracks=lambda _:True)}
        self.assertEqual(_resolve_member("",ctx)[0].id, 2)
        self.assertEqual(_resolve_member("Sezin",ctx)[0].id, 2)
        answer=asyncio.run(_async_get_member_info("", "game_history", ctx))
        self.assertIn("Game A", answer)
        self.assertNotIn("Song", answer)

    def test_spotify_song_and_link_are_read_from_live_activity(self):
        import asyncio
        import discord
        from bot.llm.tools.member_info import _async_get_member_info
        import time
        now_ms = int(time.time() * 1000)
        activity=discord.Spotify(state="Artist",details="Song",sync_id="track123",session_id="test-session",assets={"large_text":"Album"},timestamps={"start":now_ms-1000,"end":now_ms+180000})
        member=SimpleNamespace(id=2,name="listener",display_name="Listener",joined_at=None,activities=[activity])
        guild=SimpleNamespace(get_member=lambda _:member,members=[member])
        ctx={"guild":guild,"author":member,"message":SimpleNamespace(mentions=[],content="what am I listening to")}
        answer=asyncio.run(_async_get_member_info("","current_music",ctx))
        self.assertIn("Song",answer)
        self.assertIn("Artist",answer)
        self.assertIn("https://open.spotify.com/track/track123",answer)

    def test_unavailable_music_explained_without_banter(self):
        import asyncio
        from bot.llm.tools.member_info import _async_get_member_info
        member=SimpleNamespace(id=2,name="listener",display_name="Listener",joined_at=None,activities=[])
        guild=SimpleNamespace(get_member=lambda _:member,members=[member])
        answer=asyncio.run(_async_get_member_info("","current_music",{"guild":guild,"author":member}))
        self.assertIn("görünmüyor",answer)


if __name__ == "__main__":
    unittest.main()
