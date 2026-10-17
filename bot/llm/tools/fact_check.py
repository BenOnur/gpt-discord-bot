"""
Server Fact-Checking and Contradiction Detector Tool ('Arşiv Dedektifi / Çelişki Tespiti').
Searches the server's historical SQLite archive and memory vault to verify or debunk claims,
detect contradictions, and prove what members actually said in the past with exact dates and quotes.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import sqlite3
from bot.archive_access import open_archive
from typing import Any

from bot.llm.tools._types import ToolEntry

_ARCHIVE_PATH = Path(os.getenv("DISCORD_MEMORY_DIR", "/var/lib/discord-ai-memory")) / "archive.sqlite3"


def fact_check(claim: str, user_name: str | None = None) -> str:
    """
    Fact-check a claim against past server message history to detect contradictions or prove statements.
    """
    clean_claim = claim.strip()
    if not clean_claim:
        return "Doğrulanacak veya çelişkisi kontrol edilecek bir iddia belirtilmedi."

    if not _ARCHIVE_PATH.exists():
        return "Sunucu mesaj arşivi henüz oluşturulmamış veya erişilemiyor."

    clean_user = user_name.strip().lstrip("@") if user_name else ""

    try:
        db = open_archive(_ARCHIVE_PATH)
        db.row_factory = sqlite3.Row

        # Try to resolve author ID if user_name is given
        target_author_id = None
        resolved_name = clean_user
        if clean_user:
            if clean_user.isdigit():
                target_author_id = int(clean_user)
            else:
                user_match = db.execute(
                    "SELECT author, name FROM messages WHERE name LIKE ? ORDER BY id DESC LIMIT 1",
                    (f"%{clean_user}%",),
                ).fetchone()
                if user_match:
                    target_author_id = user_match["author"]
                    resolved_name = user_match["name"]

        # Extract meaningful search words from the claim (filtering short/common stop words)
        stop_words = {"ve", "ile", "için", "ama", "fakat", "gibi", "kadar", "daha", "bunu", "şunu", "olan", "eden", "dedi", "etti"}
        words = [w for w in re.findall(r"\w+", clean_claim.lower()) if len(w) > 2 and w not in stop_words]

        matches = []

        # 1. Try FTS5 search
        if words:
            fts_query = " OR ".join(f'"{w}"' for w in words[:6])
            if target_author_id:
                matches = db.execute(
                    """SELECT m.name, m.author, m.created, m.content, m.channel
                       FROM message_search s
                       JOIN messages m ON m.id = s.rowid
                       WHERE message_search MATCH ? AND m.author = ?
                       ORDER BY rank LIMIT 12""",
                    (fts_query, target_author_id),
                ).fetchall()
            else:
                matches = db.execute(
                    """SELECT m.name, m.author, m.created, m.content, m.channel
                       FROM message_search s
                       JOIN messages m ON m.id = s.rowid
                       WHERE message_search MATCH ?
                       ORDER BY rank LIMIT 12""",
                    (fts_query,),
                ).fetchall()

        # 2. Fallback to LIKE search if FTS5 returned nothing
        if not matches and words:
            like_clauses = " OR ".join(["content LIKE ?"] * len(words[:4]))
            like_params = [f"%{w}%" for w in words[:4]]
            if target_author_id:
                matches = db.execute(
                    f"""SELECT name, author, created, content, channel
                        FROM messages
                        WHERE author = ? AND ({like_clauses})
                        ORDER BY id DESC LIMIT 12""",
                    (target_author_id, *like_params),
                ).fetchall()
            else:
                matches = db.execute(
                    f"""SELECT name, author, created, content, channel
                        FROM messages
                        WHERE ({like_clauses})
                        ORDER BY id DESC LIMIT 12""",
                    (*like_params,),
                ).fetchall()

        if not matches:
            user_suffix = f" ('{resolved_name}')" if resolved_name else ""
            return f"Sunucu arşivinde '{clean_claim}' iddiasıyla veya konusuyla ilgili geçmiş bir kayıt bulunamadı{user_suffix}."

        report = [
            f"🔍 **ARŞİV DEDEKTİFİ BULGULARI:**",
            f"• İddia / Konu: \"{clean_claim}\"",
        ]
        if resolved_name:
            report.append(f"• Hedef Kişi: {resolved_name}")

        report.append("\n📜 **Geçmiş Arşivden İlgili Mesajlar ve Kanıtlar:**")
        for r in matches:
            date_str = str(r["created"])[:16]
            clean_text = r["content"].replace("\n", " ")[:200]
            report.append(f"• **{r['name']}** [{date_str}]: \"{clean_text}\"")

        report.append(
            "\n[Talimat: Yukarıdaki arşiv kanıtlarını incele. "
            "Kullanıcının güncel iddiası geçmişte söyledikleriyle çelişiyor mu yoksa tutarlı mı? "
            "Eğer çelişki varsa tarih ve alıntıyla doğrudan yüzüne vur. Tutarlıysa geçmişte de aynısını söylediğini teyit et. "
            "ASLA VİRGÜL KULLANMA. Hazırcevap, net ve tam bir Discord üyesi gibi konuş.]"
        )

        return "\n".join(report)

    except Exception as e:
        logging.error("fact_check error: %s", e)
        return f"Çelişki denetimi yapılırken hata oluştu: {e}"


FACT_CHECK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fact_check",
        "description": "Fact-check statements, detect contradictions, and verify past claims using the server message archive. Use when someone says 'ben öyle demedim', 'Ahmet öyle iddia etmişti', 'yalan söylüyor mu', or when checking consistency with past statements.",
        "parameters": {
            "type": "object",
            "properties": {
                "claim": {
                    "type": "string",
                    "description": "The statement, claim, or argument to fact-check against past history",
                },
                "user_name": {
                    "type": "string",
                    "description": "Optional member username whose past statements are being scrutinized",
                },
            },
            "required": ["claim"],
        },
    },
}

TOOL_NAME = "fact_check"
TOOL_ENTRY = ToolEntry(
    schema=FACT_CHECK_SCHEMA,
    fn=fact_check,
)
