import unittest

from bot.llm.request_budget import fit_groq_messages
from bot.llm.errors import format_user_friendly_error


class RequestBudgetTests(unittest.TestCase):
    def test_old_turn_removed_with_tool_results_without_changing_source(self):
        messages = [
            {"role": "system", "content": "Keep instructions"},
            {"role": "user", "content": "old " * 7000},
            {"role": "assistant", "tool_calls": [{"id": "a"}]},
            {"role": "tool", "tool_call_id": "a", "content": "result"},
            {"role": "user", "content": "latest"},
        ]
        self.assertEqual(fit_groq_messages(messages), [messages[0], messages[-1]])
        self.assertEqual(len(messages), 5)

    def test_current_tool_exchange_kept_intact(self):
        messages = [{"role": "user", "content": "question"},
                    {"role": "assistant", "tool_calls": [{"id": "b"}]},
                    {"role": "tool", "tool_call_id": "b", "content": "result"}]
        self.assertEqual(fit_groq_messages(messages), messages)

    def test_oversized_latest_or_schemas_fail_without_losing_instructions(self):
        for messages, tools in (([{"role": "user", "content": "x " * 7000}], []),
                                ([{"role": "user", "content": "hi"}], [{"description": "x " * 7000}])):
            with self.assertRaises(ValueError):
                fit_groq_messages(messages, tools)

    def test_errors_are_safe_turkish_messages(self):
        for status in (401, 403, 404, 413, 429):
            result = format_user_friendly_error(Exception(str(status)))
            self.assertTrue(result)
            self.assertFalse(any("\u4e00" <= char <= "\u9fff" for char in result))
