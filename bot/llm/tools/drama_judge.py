"""
Server Court / Drama Judge tool ('Sunucu Mahkemesi / Racon Kesici').
Collects archive evidence between disputing server members and structures the court docket for judgment.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import sqlite3

from bot.llm.tools._types import ToolEntry

_ARCHIVE_PATH = Path(os.getenv("DISCORD_MEMORY_DIR", "/var/lib/discord-ai-memory")) / "archive.sqlite3"


def drama_judge(plaintiff: str, defendant: str, claim: str, keywords: str | None = None) -> str:
    """
    Gather archive evidence and set up the court docket for resolving a dispute between two server members.
    """
    clean_plaintiff = plaintiff.strip().lstrip("@")
    clean_defendant = defendant.strip().lstrip("@")
    clean_claim = claim.strip()

    if not _ARCHIVE_PATH.exists():
        evidence_text = "Sunucu arşivi henüz hazır değil; beyanlara dayalı karar verilecek."
    else:
        try:
            db = sqlite3.connect(_ARCHIVE_PATH, timeout=5)
            db.row_factory = sqlite3.Row

            evidence = []
            # Search relevant quotes using keywords or claim words
            search_terms = (keywords or clean_claim).split()[:4]
            query_parts = [f"%{term}%" for term in search_terms if len(term) > 2]

            for term in query_parts:
                rows = db.execute(
                    """SELECT name, content, created FROM messages
                       WHERE (name LIKE ? OR name LIKE ? OR content LIKE ?)
                       ORDER BY id DESC LIMIT 5""",
                    (f"%{clean_plaintiff}%", f"%{clean_defendant}%", term),
                ).fetchall()
                for r in rows:
                    evidence.append(f"• **{r['name']}** [{str(r['created'])[:16]}]: \"{r['content'][:180]}\"")
                if len(evidence) >= 8:
                    break

            if evidence:
                evidence_text = "\n".join(evidence[:8])
            else:
                evidence_text = "Sunucu arşivinde doğrudan yazılı bir delil bulunamadı; tarafların samimiyetine bakılacak."
        except Exception as e:
            logging.error("drama_judge evidence lookup error: %s", e)
            evidence_text = f"Arşiv delil taramasında hata oluştu: {e}"

    docket = [
        "⚖️ **T.C. DİSCORD SUNUCU MAHKEMESİ DAVA DOSYASI**",
        f"• **Hakim / Mahkeme Başkanı:** @orospuevladı",
        f"• **Davacı:** @{clean_plaintiff}",
        f"• **Davalı:** @{clean_defendant}",
        f"• **Dava Konusu / İddia:** {clean_claim}",
        "\n📑 **Sunucu Arşivinden Çıkarılan Deliller:**",
        evidence_text,
        "\n[Talimat: Mahkeme Başkanı olarak delilleri ve tarafların iddialarını incele. "
        "Kendi hazırcevap, tarafsız, virgülsüz, hafif iğneleyici ve komik üslubunla HÜKMÜNÜ ver. "
        "Kim haklı, kim haksız açıkla. Haksız tarafa yaratıcı, komik bir sunucu cezası kes.]",
    ]

    return "\n".join(docket)


DRAMA_JUDGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "drama_judge",
        "description": "Hold a server court ('Sunucu Mahkemesi') to resolve disputes, arguments, or bets between two members, examine evidence, and hand down a verdict and funny punishment.",
        "parameters": {
            "type": "object",
            "properties": {
                "plaintiff": {
                    "type": "string",
                    "description": "Name or handle of the plaintiff (davacı)",
                },
                "defendant": {
                    "type": "string",
                    "description": "Name or handle of the defendant (davalı)",
                },
                "claim": {
                    "type": "string",
                    "description": "Description of the dispute, bet, or accusation",
                },
                "keywords": {
                    "type": "string",
                    "description": "Optional search keywords to locate related archive proof/messages",
                },
            },
            "required": ["plaintiff", "defendant", "claim"],
        },
    },
}

TOOL_NAME = "drama_judge"
TOOL_ENTRY = ToolEntry(
    schema=DRAMA_JUDGE_SCHEMA,
    fn=drama_judge,
)
