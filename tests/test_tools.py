import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from bot.llm.tools.calculate import calculate
from bot.llm.tools.time_tool import get_current_time
from bot.llm.tools.wikipedia import wikipedia_search
from bot.llm.tools.web_search import format_search_results, web_search
from bot.llm.tools.read_url import read_url
from bot.llm.tools.reminder import set_reminder, restore_pending_reminders
from bot.llm.tools.get_user_profile import get_user_profile
from bot.llm.tools.server_history import search_server_history
from bot.llm.tools.react import react_to_message
from bot.llm.tools.discord_ctx import set_current_discord_context
from bot.llm.tools.registry import build_tool_registry
from bot.chat.command_intents import trivia_intent
from bot.llm.tools.profile_avatar import looks_like_avatar_request, _resolve_member


class ToolsTest(unittest.TestCase):
    def test_calculate_basic(self):
        self.assertIn("4", calculate("2 + 2"))
        self.assertIn("180", calculate("150 * 1.2"))
        self.assertIn("12", calculate("sqrt(144)"))
        self.assertIn("1024", calculate("2 ** 10"))

    def test_calculate_error_handling(self):
        res = calculate("import os")
        self.assertTrue(res.startswith("Hesaplama hatası"))

    def test_time_tool(self):
        res = get_current_time("Europe/Istanbul")
        self.assertIn("Tarih ve Saat", res)
        self.assertIn("Europe/Istanbul", res)

    def test_wikipedia_search_mock(self):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "title": "Python",
                "description": "Programlama dili",
                "extract": "Python yüksek seviyeli bir dildir.",
                "content_urls": {"desktop": {"page": "https://tr.wikipedia.org/wiki/Python"}},
            }
            mock_get.return_value = mock_resp
            res = wikipedia_search("Python")
            self.assertIn("Python", res)
            self.assertIn("Programlama dili", res)

    def test_web_search_formatter(self):
        raw = {
            "web": {
                "results": [
                    {
                        "title": "Test Title",
                        "url": "https://example.com",
                        "description": "Test snippet description",
                    }
                ]
            }
        }
        formatted = format_search_results(raw, "test query")
        self.assertIn('Search results for "test query":', formatted)
        self.assertIn("Test Title", formatted)
        self.assertIn("https://example.com", formatted)
        self.assertIn("Test snippet description", formatted)

    def test_read_url_web_page_mock(self):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b"<html><head><title>Haber Basligi</title></head><body><p>Bu onemli bir haber metnidir ve detaylar icerir.</p></body></html>"
            mock_get.return_value = mock_resp
            res = read_url("https://example.com/haber")
            self.assertIn("Haber Basligi", res)
            self.assertIn("onemli bir haber metnidir", res)

    def test_read_url_tweet_mock(self):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "author_name": "Elon Musk",
                "html": "<blockquote><p>Starship flight test was successful!</p></blockquote>",
            }
            mock_get.return_value = mock_resp
            res = read_url("https://x.com/elonmusk/status/123456789")
            self.assertIn("Elon Musk", res)
            self.assertIn("Starship flight test", res)

    def test_reminder_tool_with_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("bot.llm.tools.reminder._DB_PATH", Path(tmpdir) / "reminders.sqlite3"):
                mock_channel = MagicMock(id=111)
                mock_author = MagicMock(id=222)
                mock_bot = MagicMock()
                mock_bot.loop.is_running.return_value = False
                mock_scheduler = MagicMock()

                set_current_discord_context({
                    "channel": mock_channel,
                    "author": mock_author,
                    "bot": mock_bot,
                    "scheduler": mock_scheduler,
                })

                res = set_reminder(15, "çayı kapat")
                self.assertIn("Hatırlatıcı kuruldu", res)
                self.assertIn("15", res)
                self.assertIn("çayı kapat", res)
                mock_scheduler.add_job.assert_called_once()

    def test_react_tool_without_context(self):
        set_current_discord_context({})
        res = react_to_message("💀")
        self.assertIn("Discord mesaj bağlamı bulunamadı", res)

    def test_daily_recap_basic(self):
        from bot.llm.tools.daily_recap import daily_recap
        res = daily_recap(24)
        self.assertTrue(isinstance(res, str))
        self.assertTrue(len(res) > 0)

    def test_drama_judge_basic(self):
        from bot.llm.tools.drama_judge import drama_judge
        res = drama_judge("Ahmet", "Mehmet", "CS2 maçında infoyu bilerek yanlış verdi")
        self.assertIn("DİSCORD SUNUCU MAHKEMESİ", res)
        self.assertIn("Ahmet", res)
        self.assertIn("Mehmet", res)

    def test_voice_speak_basic(self):
        from bot.llm.tools.voice_speak import voice_speak
        # Empty text
        self.assertIn("Söylenecek bir metin belirtilmedi", voice_speak(""))
        # Without Discord loop
        res = voice_speak("Ahmet ayağını denk al")
        self.assertIn("Ses kanalına bağlanılamadı", res)

    def test_generate_quote_basic(self):
        from bot.llm.tools.generate_quote import generate_quote
        # Without Discord loop
        res = generate_quote("Örnek racon sözü", "Sezin")
        self.assertIn("Quote kartı üretildi", res)

    def test_quote_card_renderer(self):
        from bot.quote.renderer import render_quote_card
        png_bytes = render_quote_card("Hayat kısa kuşlar uçuyor", "Cemal Süreya")
        self.assertTrue(isinstance(png_bytes, bytes))
        self.assertTrue(len(png_bytes) > 1000)
        # Check PNG header
        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_server_trivia_basic(self):
        from bot.llm.tools.trivia import server_trivia
        # Without Discord loop
        res = server_trivia(query="cs2")
        self.assertIn("Server trivia başlatıldı", res)

    def test_natural_language_command_intents(self):
        self.assertEqual(trivia_intent("kimdedi oyununu oynatsana bize"), "play")
        self.assertEqual(trivia_intent("@bot kimdedi sıralamasını göstersene"), "leaderboard")
        self.assertIsNone(trivia_intent("kimdedi komutu nasıl kullanılıyor"))

    def test_profile_avatar_natural_language_detection(self):
        self.assertTrue(looks_like_avatar_request("@orospuevladı @Onur'un profil fotoğrafını göndersene"))
        self.assertTrue(looks_like_avatar_request("Apro'nun avatarını atsana"))
        self.assertFalse(looks_like_avatar_request("Profil fotoğrafımı nasıl değiştiririm?"))

    def test_profile_avatar_resolution_prefers_tagged_member(self):
        onur = SimpleNamespace(id=1, name="onur", display_name="Onur", global_name="Onur")
        bot_user = SimpleNamespace(id=99)
        ctx = {
            "guild": SimpleNamespace(members=[onur]),
            "message": SimpleNamespace(mentions=[bot_user, onur], content="profil fotoğrafını gönder"),
            "bot": SimpleNamespace(user=bot_user),
        }
        member, error = _resolve_member(None, ctx)
        self.assertIs(member, onur)
        self.assertIsNone(error)

    def test_profile_avatar_resolution_reports_ambiguous_names(self):
        first = SimpleNamespace(id=1, name="onur_a", display_name="Onur", global_name=None)
        second = SimpleNamespace(id=2, name="onur_b", display_name="Onur", global_name=None)
        guild = SimpleNamespace(members=[first, second], owner_id=3, get_member=lambda _id: None)
        ctx = {"guild": guild, "message": SimpleNamespace(mentions=[], content=""), "bot": None}
        member, error = _resolve_member("Onur", ctx)
        self.assertIsNone(member)
        self.assertIn("birden fazla", error)

    def test_tool_registry_discovery(self):
        registry = build_tool_registry()
        expected = [
            "web_search",
            "get_weather",
            "get_market_prices",
            "visuals_core",
            "calculate",
            "get_current_time",
            "wikipedia_search",
            "set_reminder",
            "read_url",
            "get_user_profile",
            "search_server_history",
            "react_to_message",
            "daily_recap",
            "drama_judge",
            "voice_speak",
            "generate_quote",
            "server_trivia",
            "send_profile_avatar",
        ]
        for tool_name in expected:
            self.assertIn(tool_name, registry, f"Tool '{tool_name}' should be in registry")
            self.assertIsNotNone(registry[tool_name].schema)
            self.assertIsNotNone(registry[tool_name].fn)


if __name__ == "__main__":
    unittest.main()

