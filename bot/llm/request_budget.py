"""Keep small Groq free-tier requests below their per-minute token ceiling."""
import json

import tiktoken


def fit_groq_messages(messages, tools=(), budget=5000):
    """Drop oldest complete turns; preserve system instructions and latest turn.

    Includes tool schemas and message metadata in the estimate. Leaves room for
    output tokens and differences between the local and provider tokenizers.
    Never mutates the caller's history or separates tool calls from results.
    """
    encoding = tiktoken.get_encoding("o200k_base")
    fixed = []
    turns = []
    for message in messages:
        if message["role"] in ("system", "developer"):
            fixed.append(message)
        elif message["role"] == "user" or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    while True:
        result = fixed + [message for turn in turns for message in turn]
        payload = json.dumps({"messages": result, "tools": tools}, ensure_ascii=False)
        if len(encoding.encode(payload, disallowed_special=())) <= budget:
            return result
        if len(turns) <= 1:
            raise ValueError("İstek Groq ücretsiz token sınırına sığmıyor; daha kısa bir mesajla yeniden dene.")
        turns.pop(0)


def is_small_groq_model(client, model):
    return client.base_url.host == "api.groq.com" and model in (
        "openai/gpt-oss-120b", "openai/gpt-oss-20b",
    )
