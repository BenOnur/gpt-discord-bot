"""Find indexed image descriptions and return original Discord source messages."""
import sqlite3
from pathlib import Path

from bot.archive_access import allowed_channels
from bot.llm.tools._types import ToolEntry
from bot.llm.tools.discord_ctx import get_current_discord_context


def search_media(query: str) -> str:
    ctx = get_current_discord_context()
    guild = ctx.get("guild")
    allowed = sorted(allowed_channels(ctx))
    if not guild or not allowed:
        return "Bu kanalda görsel arşivine erişilemiyor."
    path = Path(ctx.get("config", {}).get("memory", {}).get("data_dir", "/var/lib/discord-ai-memory")) / "archive.sqlite3"
    if not path.exists():
        return "Görsel arşivi henüz hazır değil."
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        slots = ",".join("?" for _ in allowed)
        import re
        words = re.findall(r"\w+", query)[:6]
        if not words:
            return "Aranacak görseli tarif et."
        conditions = " OR ".join("description LIKE ?" for _ in words)
        rows = db.execute(f"SELECT message,channel,description FROM media WHERE guild=? AND channel IN ({slots}) AND analyzed=1 AND ({conditions}) ORDER BY message DESC LIMIT 6",
                          (guild.id,*allowed,*[f"%{word}%" for word in words])).fetchall()
    return "\n".join(f"{description}\nhttps://discord.com/channels/{guild.id}/{channel}/{message}" for message,channel,description in rows) or "İndekslenmiş görsellerde eşleşme yok. Bu sonuç tüm eski görsellerin tarandığı anlamına gelmez."


TOOL_NAME = "search_media"
TOOL_ENTRY = ToolEntry(schema={"type":"function","function":{
    "name":TOOL_NAME,"description":"Search previously indexed server image captions and OCR. Returns source Discord message links, not guessed relationships or face identities. Indexing is gradual and incomplete.",
    "parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}
}},fn=search_media)
