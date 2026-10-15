import unittest
from unittest.mock import patch, MagicMock

from bot.llm.tools.calculate import calculate
from bot.llm.tools.time_tool import get_current_time
from bot.llm.tools.wikipedia import wikipedia_search
from bot.llm.tools.web_search import format_search_results, web_search
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
        ]
        for tool_name in expected:
            self.assertIn(tool_name, registry, f"Tool '{tool_name}' should be in registry")
            self.assertIsNotNone(registry[tool_name].schema)
            self.assertIsNotNone(registry[tool_name].fn)


if __name__ == "__main__":
    unittest.main()
