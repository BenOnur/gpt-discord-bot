from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as Obj
import unittest
import discord
from bot.llm.tools.member_info import current_music_report, live_activity_snapshot


class LivePresenceTests(unittest.TestCase):
    def test_non_spotify_listening_fields_are_available(self):
        member = Obj(display_name="Onur", activities=[discord.Activity(
            name="Other Music", type=discord.ActivityType.listening,
            details="Current Track", state="Current Artist")])
        report, query = current_music_report(member)
        self.assertIn("Other Music", report)
        self.assertEqual(query, "Current Track Current Artist")

    def test_music_apps_publishing_playing_keep_uncertainty(self):
        member = Obj(display_name="Onur", activities=[discord.Activity(
            name="Music Player", type=discord.ActivityType.playing,
            details="Track B", state="Artist B")])
        report, query = current_music_report(member)
        self.assertEqual(query, "Track B Artist B")
        self.assertIn("kesin", report)

    def test_multiple_apps_never_silently_prefer_spotify(self):
        activities = [discord.Activity(name=name, type=discord.ActivityType.listening,
                      details="song", state="artist") for name in ["Spotify", "Other Music"]]
        report, query = current_music_report(Obj(display_name="Onur", activities=activities))
        self.assertIsNone(query)
        self.assertIn("Birden fazla", report)

    def test_expired_spotify_is_not_current(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        spotify = discord.Spotify(state="Old Artist", details="Old Track",
            timestamps={"start": int((past-timedelta(minutes=3)).timestamp()*1000),
                        "end": int(past.timestamp()*1000)},
            assets={"large_text":"Old Album"}, sync_id="old", session_id="test")
        report, query = current_music_report(Obj(display_name="Onur", activities=[spotify]))
        self.assertIsNone(query)
        self.assertNotIn("Old Track", report)

    def test_custom_status_is_not_a_music_source(self):
        member = Obj(display_name="Onur", activities=[discord.CustomActivity(name="song text")])
        self.assertEqual(live_activity_snapshot(member), [])
        self.assertIsNone(current_music_report(member)[1])
