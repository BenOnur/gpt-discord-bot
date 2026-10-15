"""
Daily server recap tool ('Günün Sunucu Gazetesi / Bülteni').
Analyzes recent conversations, chatterbox rankings, inside jokes, and highlights from the SQLite archive.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict

from bot.llm.tools._types import ToolEntry

_ARCHIVE_PATH = Path(os.getenv("DISCORD_MEMORY_DIR", "/var/lib/discord-ai-memory")) / "archive.sqlite3"


def daily_recap(hours: int = 24) -> str:
    """
    Generate a briefing of recent server activity to create the daily server newspaper/bulletin.
    """
    if not _ARCHIVE_PATH.exists():
        return "Sunucu mesaj arşivi henüz oluşturulmamış veya erişilemiyor."

    try:
        db = sqlite3.connect(_ARCHIVE_PATH, timeout=5)
        db.row_factory = sqlite3.Row

        # Calculate time threshold (UTC)
        since_time = (datetime.now(timezone.utc) - timedelta(hours=max(1, hours))).isoformat()

        # Query messages from the specified time window
        rows = db.execute(
            """SELECT id, channel, author, name, content, created, url
               FROM messages
               WHERE created >= ?
               ORDER BY id DESC LIMIT 300""",
            (since_time,),
        ).fetchall()

        # If very few messages in the window, fallback to the last 100 messages overall
        is_fallback = False
        if len(rows) < 15:
            rows = db.execute(
                """SELECT id, channel, author, name, content, created, url
                   FROM messages
                   ORDER BY id DESC LIMIT 100""",
            ).fetchall()
            is_fallback = True

        if not rows:
            return "Sunucuda analiz edilecek yeterli mesaj bulunamadı."

        authors = [r["name"] or r["author"] for r in rows if r["name"] or r["author"]]
        author_counts = Counter(authors).most_common(5)

        # Highlight messages (e.g. longer messages or messages with punchlines)
        highlight_candidates = [
            r for r in rows
            if len(r["content"]) > 30 and not r["content"].startswith("http")
        ]
        sample_highlights = highlight_candidates[:8]

        report = []
        if is_fallback:
            report.append("📰 **GÜNÜN SUNUCU VERİLERİ (Son Mesaj Arşivi):**")
        else:
            report.append(f"📰 **GÜNÜN SUNUCU VERİLERİ (Son {hours} Saat):**")

        report.append(f"• Toplam Taranan Mesaj: {len(rows)}")
        report.append("\n👑 **En Çok Çene Çalanlar / Gevezeler:**")
        for rank, (name, count) in enumerate(author_counts, 1):
            report.append(f"  {rank}. {name}: {count} mesaj")

        if sample_highlights:
            report.append("\n💬 **Günün Dikkat Çeken Replikleri ve Olayları:**")
            for h in sample_highlights:
                c_clean = h["content"].replace("\n", " ")[:160]
                report.append(f"• **{h['name']}**: \"{c_clean}\"")

        report.append(
            "\n[Talimat: Bu verileri kullanarak 'Günün Sunucu Gazetesi' başlığıyla esprili, hazırcevap, "
            "virgülsüz, Discord genç jargonunda ve kendi kanka üslubunla eğlenceli bir bülten çıkar. "
            "Günün MVP'sini, en çok boş yapanını ve günün olayını belirle.]"
        )

        return "\n".join(report)
    except Exception as e:
        logging.error("daily_recap error: %s", e)
        return f"Günlük özet verisi çıkarılırken hata oluştu: {e}"


DAILY_RECAP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "daily_recap",
        "description": "Generate the daily server newspaper/recap ('Günün Sunucu Gazetesi') analyzing recent chats, chatterbox members, highlights, and drama.",
        "parameters": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "How many hours back to analyze (default 24)",
                    "default": 24,
                }
            },
        },
    },
}

TOOL_NAME = "daily_recap"
TOOL_ENTRY = ToolEntry(
    schema=DAILY_RECAP_SCHEMA,
    fn=daily_recap,
)
