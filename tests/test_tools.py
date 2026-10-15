import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

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
        ]
        for tool_name in expected:
            self.assertIn(tool_name, registry, f"Tool '{tool_name}' should be in registry")
            self.assertIsNotNone(registry[tool_name].schema)
            self.assertIsNotNone(registry[tool_name].fn)


if __name__ == "__main__":
    unittest.main()
