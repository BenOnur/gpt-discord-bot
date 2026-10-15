"""
Server user profile and history analyzer tool.
Queries the SQLite archive and Basic Memory notes to summarize a server member's activity,
characteristic quotes, frequent topics, and inside jokes.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import sqlite3
from typing import Any

from bot.llm.tools._types import ToolEntry

_ARCHIVE_PATH = Path(os.getenv("DISCORD_MEMORY_DIR", "/var/lib/discord-ai-memory")) / "archive.sqlite3"


def get_user_profile(user_query: str) -> str:
    """Analyze a server member's activity, speaking habits, and recent quotes."""
    clean_query = user_query.strip().lstrip("@")
    if not clean_query:
        return "Lütfen profili incelenecek bir kullanıcı adı veya ID belirtin."

    if not _ARCHIVE_PATH.exists():
        return "Sunucu mesaj arşivi henüz oluşturulmamış veya erişilemiyor."

    try:
        db = sqlite3.connect(_ARCHIVE_PATH, timeout=5)
        db.row_factory = sqlite3.Row

        # Try search by Discord ID if numeric, else by display name
        if clean_query.isdigit():
            user_rows = db.execute(
                "SELECT * FROM messages WHERE author = ? ORDER BY id DESC LIMIT 20",
                (int(clean_query),),
            ).fetchall()
            count_row = db.execute(
                "SELECT count(*), min(created), max(created), name FROM messages WHERE author = ?",
                (int(clean_query),),
            ).fetchone()
        else:
            user_rows = db.execute(
                "SELECT * FROM messages WHERE name LIKE ? ORDER BY id DESC LIMIT 20",
                (f"%{clean_query}%",),
            ).fetchall()
            count_row = db.execute(
                "SELECT count(*), min(created), max(created), name FROM messages WHERE name LIKE ?",
                (f"%{clean_query}%",),
            ).fetchone()

        if not user_rows or not count_row or count_row[0] == 0:
            return f"'{clean_query}' adına ait sunucu arşivinde mesaj bulunamadı."

        total_msgs = count_row[0]
        first_date = str(count_row[1])[:10] if count_row[1] else "Bilinmiyor"
        last_date = str(count_row[2])[:10] if count_row[2] else "Bilinmiyor"
        user_name = count_row[3] or clean_query
        author_id = user_rows[0]["author"]

        # Channel activity distribution
        top_channels = db.execute(
            "SELECT channel, count(*) as cnt FROM messages WHERE author = ? GROUP BY channel ORDER BY cnt DESC LIMIT 3",
            (author_id,),
        ).fetchall()

        # Select distinct sample quotes (filtering out very short messages)
        sample_quotes = []
        for r in user_rows:
            content = r["content"].strip()
            if len(content) > 10 and not content.startswith(("http://", "https://")):
                sample_quotes.append(f"- \"{content}\" [{str(r['created'])[:16]}]")
            if len(sample_quotes) >= 8:
                break

        lines = [
            f"👤 Sunucu Üye Profili: {user_name} (Discord ID: {author_id})",
            f"• Toplam Arşivlenen Mesaj: {total_msgs}",
            f"• İlk Görülme: {first_date} | Son Görülme: {last_date}",
            f"• En Aktif Kanal ID'leri: {', '.join(str(c['channel']) for c in top_channels)}",
            "",
            "Karakteristik / Son Mesaj Alıntıları:",
        ]
        lines.extend(sample_quotes or ["(Yeterli uzunlukta mesaj alıntısı bulunamadı)"])

        return "\n".join(lines)
    except Exception as e:
        logging.error("get_user_profile error: %s", e)
        return f"Kullanıcı profili çıkarılırken hata oluştu: {e}"


USER_PROFILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_user_profile",
        "description": "Analyze a server member's profile, total message count, activity, and sample quotes from the server message archive. Use when asked 'beni anlat', 'şu kimdir', or to learn someone's background in the server.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_query": {
                    "type": "string",
                    "description": "Username or Discord ID of the member to look up",
                }
            },
            "required": ["user_query"],
        },
    },
}

TOOL_NAME = "get_user_profile"
TOOL_ENTRY = ToolEntry(
    schema=USER_PROFILE_SCHEMA,
    fn=get_user_profile,
)
