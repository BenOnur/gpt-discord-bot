import unittest
from bot.llm.chatgpt_plan import response_payload, ChatGPTPlanClient


class ChatGPTPlanTests(unittest.TestCase):
    def test_system_images_and_tool_history_are_converted(self):
        result = response_payload("account-model", [
            {"role": "system", "content": "persona"},
            {"role": "user", "content": [{"type": "text", "text": "question"},
                {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]},
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "calculate", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "7"}],
            [{"type": "function", "function": {"name": "calculate", "parameters": {"type": "object"}}}])
        self.assertEqual(result["instructions"], "persona")
        self.assertFalse(result["store"])
        self.assertTrue(result["stream"])
        self.assertEqual(result["input"][0]["content"][1]["type"], "input_image")
        self.assertEqual(result["input"][1]["namespace"], "discord")
        self.assertEqual(result["input"][2]["call_id"], "c1")
        self.assertEqual(result["tools"][0]["type"], "namespace")
        self.assertNotIn("temperature", result)
        self.assertEqual(result["reasoning"], {"effort": "low"})

    def test_reasoning_and_function_items_survive_tool_roundtrip(self):
        prior = [{"type": "reasoning", "id": "r1", "encrypted_content": "opaque"},
                 {"type": "function_call", "call_id": "c1", "name": "calculate", "arguments": "{}"}]
        result = response_payload("gpt-6-luna", [{"role": "assistant", "responses_output": prior}], [])
        self.assertEqual(result["input"], prior)

    def test_no_account_usage_without_explicit_allowed_user(self):
        import asyncio
        client = ChatGPTPlanClient({"credentials_path": "/never-read-this"})
        with self.assertRaisesRegex(RuntimeError, "etkin değil"):
            asyncio.run(client.token())


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, events):
        from types import SimpleNamespace as Obj
        from unittest.mock import AsyncMock, MagicMock, patch
        class Events:
            def __aiter__(self):
                async def iterate():
                    for event in events:
                        yield event
                return iterate()
            close = AsyncMock()
        mock = MagicMock()
        mock.responses.create = AsyncMock(return_value=Events())
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=mock)
        manager.__aexit__ = AsyncMock(return_value=None)
        client = ChatGPTPlanClient({"credentials_path": "/not-read"})
        client.token = AsyncMock(return_value="synthetic-token")
        with patch("bot.llm.chatgpt_plan.AsyncOpenAI", return_value=manager):
            return await client.create(model="gpt-6-luna", messages=[{"role":"user", "content":"test"}])

    async def test_partial_stream_is_not_success(self):
        from types import SimpleNamespace as Obj
        with self.assertRaisesRegex(RuntimeError, "kanıtı"):
            await self.exercise([Obj(type="response.output_text.delta", delta="partial")])

    async def test_quota_error_after_stream_start_is_failure(self):
        from types import SimpleNamespace as Obj
        with self.assertRaisesRegex(RuntimeError, "usage_limit"):
            await self.exercise([Obj(type="response.failed", response=Obj(error=Obj(code="subscription_sharing_usage_limit_exceeded")))])

    async def test_completed_response_becomes_existing_tool_loop_message(self):
        from types import SimpleNamespace as Obj
        from unittest.mock import MagicMock
        item = MagicMock()
        item.type = "function_call"
        item.call_id, item.name, item.arguments = "call1", "calculate", '{"expression":"3+4"}'
        item.model_dump.return_value = {"type":"function_call", "call_id":"call1", "name":"calculate", "arguments":item.arguments}
        result = await self.exercise([Obj(type="response.completed", response=Obj(id="r1", output=[item]))])
        self.assertEqual(result.choices[0].message.tool_calls[0].function.name, "calculate")
        self.assertTrue(result.choices[0].message.model_dump()["responses_output"])
