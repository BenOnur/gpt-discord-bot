"""
Server message history search tool using SQLite FTS5 full-text index.
Allows looking up past conversations, inside jokes, and exact quotes across server channels.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import sqlite3

from bot.llm.tools._types import ToolEntry

_ARCHIVE_PATH = Path(os.getenv("DISCORD_MEMORY_DIR", "/var/lib/discord-ai-memory")) / "archive.sqlite3"


def search_server_history(query: str) -> str:
    """Search server history for exact keywords, past events, or jokes."""
    clean_query = query.strip()
    if not clean_query:
        return "Arama için bir kelime veya konu belirtilmedi."

    if not _ARCHIVE_PATH.exists():
        return "Sunucu mesaj arşivi henüz oluşturulmamış veya erişilemiyor."

    try:
        db = sqlite3.connect(_ARCHIVE_PATH, timeout=5)
        db.row_factory = sqlite3.Row

        # Try FTS5 search
        safe_query = " ".join(f'"{w}"' for w in clean_query.split() if len(w) > 1)
        matches = db.execute(
            """SELECT m.name, m.author, m.created, m.content, m.url
               FROM message_search s
               JOIN messages m ON m.id = s.rowid
               WHERE message_search MATCH ?
               ORDER BY rank LIMIT 10""",
            (safe_query,),
        ).fetchall()

        if not matches:
            # Fallback to standard LIKE
            matches = db.execute(
                """SELECT name, author, created, content, url
                   FROM messages
                   WHERE content LIKE ?
                   ORDER BY id DESC LIMIT 10""",
                (f"%{clean_query}%",),
            ).fetchall()

        if not matches:
            return f"Sunucu arşivinde '{clean_query}' ile eşleşen mesaj bulunamadı."

        lines = [f"📜 Sunucu Arşiv Eşleşmeleri ('{clean_query}'):"]
        for r in matches:
            date_str = str(r["created"])[:16]
            lines.append(f"- **{r['name']}** [{date_str}]: \"{r['content'][:250]}\"")

        return "\n".join(lines)
    except Exception as e:
        logging.error("search_server_history error: %s", e)
        return f"Sunucu geçmişinde arama yapılırken hata oluştu: {e}"


SERVER_HISTORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_server_history",
        "description": "Search the entire server message archive for past discussions, specific quotes, shared jokes, or topics discussed in channels.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword, phrase, or topic to search for in server history",
                }
            },
            "required": ["query"],
        },
    },
}

TOOL_NAME = "search_server_history"
TOOL_ENTRY = ToolEntry(
    schema=SERVER_HISTORY_SCHEMA,
    fn=search_server_history,
)
