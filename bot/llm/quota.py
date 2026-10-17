"""Shared, persistent Groq minute reservations for chat and background jobs."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import time


def reserve(path, model, tokens, priority, now=None):
    now = time.time() if now is None else now
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS reservations(id INTEGER PRIMARY KEY,model TEXT,tokens INTEGER,created REAL,priority TEXT)")
        db.execute("DELETE FROM reservations WHERE created<?", (now-60,))
        rows = db.execute("SELECT tokens,priority FROM reservations WHERE model=?", (model,)).fetchall()
        if priority == "background" and any(row[1] == "interactive" for row in rows):
            raise RuntimeError("429 Sohbet kotası öncelikli; arka plan işi sonraki döngüyü bekleyecek.")
        if len(rows) >= 25 or sum(row[0] for row in rows) + tokens > 7600:
            raise RuntimeError("429 Groq ortak dakika bütçesi dolu; diğer sağlayıcıya geç.")
        db.execute("INSERT INTO reservations(model,tokens,created,priority) VALUES(?,?,?,?)", (model,tokens,now,priority))
        db.commit()
    finally:
        db.close()


async def completion(client, priority="interactive", **kwargs):
    if client.base_url.host == "api.groq.com" and kwargs.get("model") in ("openai/gpt-oss-120b","openai/gpt-oss-20b"):
        import tiktoken
        encoding=tiktoken.get_encoding("o200k_base")
        payload=json.dumps({"messages":kwargs.get("messages",[]),"tools":kwargs.get("tools",[])},ensure_ascii=False)
        output=kwargs.get("max_tokens") or (kwargs.get("extra_body") or {}).get("max_tokens",2048)
        tokens=int(len(encoding.encode(payload,disallowed_special=()))*1.08)+int(output)+100
        path=Path(os.getenv("DISCORD_MEMORY_DIR","/var/lib/discord-ai-memory"))/"api-budget.sqlite3"
        await asyncio.to_thread(reserve,path,kwargs["model"],tokens,priority)
    return await client.chat.completions.create(**kwargs)
