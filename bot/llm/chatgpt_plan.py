"""Optional SIWC Responses adapter. Own credentials; no Hermes state access."""
import asyncio
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import httpx
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk

TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
_TOKEN_LOCK = asyncio.Lock()


def save_credentials(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def response_payload(model, messages, tools):
    instructions, inputs = [], []
    for message in messages:
        role = message.get("role")
        content = message.get("content") or ""
        if role in ("system", "developer"):
            instructions.append(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
        elif role == "tool":
            inputs.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": content})
        elif message.get("responses_output"):
            inputs.extend(message["responses_output"])
        else:
            if content:
                parts = []
                for part in content if isinstance(content, list) else [{"type": "text", "text": content}]:
                    if part["type"] == "text":
                        parts.append({"type": "output_text" if role == "assistant" else "input_text", "text": part["text"]})
                    elif part["type"] == "image_url":
                        parts.append({"type": "input_image", "image_url": part["image_url"]["url"]})
                if parts:
                    inputs.append({"role": role, "content": parts})
            for call in message.get("tool_calls", []):
                inputs.append({"type": "function_call", "call_id": call["id"],
                               "name": call["function"]["name"], "arguments": call["function"]["arguments"],
                               "namespace": "discord"})
    payload = {"model": model, "input": inputs, "store": False, "stream": True,
               "reasoning": {"effort": "low"}, "include": ["reasoning.encrypted_content"]}
    if instructions:
        payload["instructions"] = "\n".join(instructions)
    if tools:
        payload["tools"] = [{"type": "namespace", "name": "discord", "description": "Discord assistant tools",
                             "tools": [{"type": "function", **tool["function"]} for tool in tools]}]
    return payload


class ChatGPTPlanClient:
    def __init__(self, config):
        self.config = config
        self.path = Path(config["credentials_path"])
        self.base_url = httpx.URL("https://api.openai.com/v1/")
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self._lock = _TOKEN_LOCK

    async def token(self):
        from bot.llm.tools.discord_ctx import get_current_discord_context
        ctx = get_current_discord_context()
        allowed = {int(value) for value in self.config.get("allowed_discord_user_ids", [])}
        uid = getattr(ctx.get("author"), "id", None)
        guild_id = getattr(ctx.get("guild"), "id", None)
        shared = {int(value) for value in self.config.get("allowed_guild_ids", [])}
        # Explicit configuration required; background jobs cannot silently spend plan quota.
        if uid is None or (uid not in allowed and guild_id not in shared):
            raise RuntimeError("ChatGPT abonelik bağlantısı bu Discord kullanıcısı için etkin değil.")
        async with self._lock:
            if not self.path.exists():
                raise RuntimeError("Discord için ayrı ChatGPT girişi henüz tamamlanmadı.")
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if "chatgpt.tokens.use.direct" not in data.get("scopes", []):
                raise RuntimeError("ChatGPT plan kullanım izni bulunmuyor; yeniden giriş gerekli.")
            if data["expires_at"] <= time.time() + 90:
                async with httpx.AsyncClient(timeout=25) as client:
                    response = await client.post(TOKEN_URL, data={
                        "grant_type": "refresh_token", "refresh_token": data["refresh_token"],
                        "client_id": data["client_id"], "resource": "https://api.openai.com/v1"})
                if response.status_code != 200:
                    raise RuntimeError(f"ChatGPT oturumu yenilenemedi (HTTP {response.status_code}); yeniden giriş gerekli.")
                refreshed = response.json()
                if "scope" in refreshed:
                    data["scopes"] = refreshed["scope"].split()
                data.update({key: refreshed[key] for key in ("access_token", "refresh_token", "id_token") if key in refreshed})
                data["expires_at"] = time.time() + int(refreshed["expires_in"])
                save_credentials(self.path, data)
            return data["access_token"]

    async def create(self, model, messages, tools=None, stream=False, **unused):
        if model != "gpt-6-luna":
            raise RuntimeError("Bu abonelik bağlantısı yalnız GPT-6 Luna low için yapılandırıldı.")
        token = await self.token()
        payload = response_payload(model, messages, tools or [])
        # Buffer until a completed event: a partial stream is never a successful answer.
        async with AsyncOpenAI(api_key=token, base_url=str(self.base_url), max_retries=0) as client:
            events = await client.responses.create(**payload)
            final = None
            try:
                async for event in events:
                    if event.type == "response.completed":
                        final = event.response
                    elif event.type in ("response.failed", "response.incomplete", "error"):
                        error = getattr(getattr(event, "response", None), "error", None)
                        code = getattr(error, "code", None) or event.type
                        raise RuntimeError(f"ChatGPT plan isteği tamamlanmadı: {code}")
            finally:
                await events.close()
        if final is None:
            raise RuntimeError("ChatGPT akışı tamamlanma kanıtı olmadan kapandı.")
        calls, texts = [], []
        for item in final.output:
            if item.type == "function_call":
                calls.append({"id": item.call_id, "type": "function", "function": {
                    "name": item.name, "arguments": item.arguments}})
            elif item.type == "message":
                texts.extend(part.text for part in item.content if part.type == "output_text")
        result = {"id": final.id, "object": "chat.completion", "created": int(time.time()), "model": model,
                  "choices": [{"index": 0, "finish_reason": "tool_calls" if calls else "stop",
                               "message": {"role": "assistant", "content": "\n".join(texts), "tool_calls": calls or None,
                                           "responses_output": [item.model_dump(exclude_none=True) for item in final.output]}}]}
        if not stream:
            return ChatCompletion.model_validate(result)
        async def chunks():
            yield ChatCompletionChunk.model_validate({
                "id": final.id, "object": "chat.completion.chunk", "created": result["created"], "model": model,
                "choices": [{"index": 0, "delta": {"content": "\n".join(texts)}, "finish_reason": "stop"}]})
        return chunks()
