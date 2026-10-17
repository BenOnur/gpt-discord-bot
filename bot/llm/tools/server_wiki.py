"""
Server Knowledge Base & Wiki Tool ('Kaybolmayan Sunucu Ansiklopedisi / Akıllı Bilgi Havuzu').
Retrieves past recommendations, guides, configurations, useful links, and shared knowledge
from both the SQLite archive and the Basic Memory knowledge vault.
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

_MEMORY_DIR = Path(os.getenv("DISCORD_MEMORY_DIR", "/var/lib/discord-ai-memory"))
_ARCHIVE_PATH = _MEMORY_DIR / "archive.sqlite3"
_VAULT_PATH = _MEMORY_DIR / "vault"


def server_wiki(query: str, category: str = "all") -> str:
    """
    Search the server's long-term knowledge base, guides, past recommendations, and shared links.
    """
    clean_query = query.strip()
    if not clean_query:
        return "Bilgi bankasında aramak için bir konu veya kelime belirtin."

    if not _ARCHIVE_PATH.exists() and not _VAULT_PATH.exists():
        return "Sunucu bilgi bankası henüz oluşturulmamış veya erişilemiyor."

    results = []
    clean_cat = category.strip().lower()

    # 1. Search SQLite Message Archive
    if _ARCHIVE_PATH.exists():
        try:
            db = open_archive(_ARCHIVE_PATH)
            db.row_factory = sqlite3.Row

            words = [w for w in re.findall(r"\w+", clean_query.lower()) if len(w) > 2]
            if words:
                filter_link = "link" in clean_cat or "link" in clean_query.lower() or "video" in clean_query.lower()

                matches = []
                # FTS5 search
                fts_query = " OR ".join(f'"{w}"' for w in words[:5])
                try:
                    raw_matches = db.execute(
                        """SELECT m.name, m.author, m.created, m.content, m.channel
                           FROM message_search s
                           JOIN messages m ON m.id = s.rowid
                           WHERE message_search MATCH ?
                           ORDER BY rank LIMIT 15""",
                        (fts_query,),
                    ).fetchall()
                    if filter_link:
                        raw_matches = [m for m in raw_matches if "http://" in m["content"] or "https://" in m["content"]]
                    matches = raw_matches[:8]
                except Exception:
                    matches = []

                # Fallback LIKE
                if not matches:
                    like_clauses = " OR ".join(["content LIKE ?"] * len(words[:3]))
                    like_params = [f"%{w}%" for w in words[:3]]
                    extra_filter = " AND (content LIKE '%http://%' OR content LIKE '%https://%')" if filter_link else ""
                    matches = db.execute(
                        f"""SELECT name, author, created, content, channel
                            FROM messages
                            WHERE ({like_clauses}) {extra_filter}
                            ORDER BY id DESC LIMIT 8""",
                        (*like_params,),
                    ).fetchall()

                for m in matches:
                    date_str = str(m["created"])[:10]
                    clean_c = m["content"].replace("\n", " ")[:220]
                    results.append(f"• **{m['name']}** [{date_str}]: \"{clean_c}\"")

        except Exception as e:
            logging.error("server_wiki sqlite search error: %s", e)

    # 2. Search Basic Memory Vault Markdown Files
    vault_matches = []
    if _VAULT_PATH.exists():
        try:
            search_regex = re.compile(re.escape(clean_query), re.IGNORECASE)
            for md_file in _VAULT_PATH.rglob("*.md"):
                try:
                    from bot.archive_access import allowed_channels
                    from bot.llm.tools.discord_ctx import get_current_discord_context
                    ctx = get_current_discord_context()
                    guild_id = getattr(ctx.get("guild"), "id", 0)
                    parts = md_file.relative_to(_VAULT_PATH).parts
                    if len(parts) < 3 or parts[0] != f"guild-{guild_id}" or not parts[1].startswith("channel-"):
                        continue
                    if int(parts[1].removeprefix("channel-")) not in allowed_channels(ctx):
                        continue
                    text = md_file.read_text(encoding="utf-8", errors="ignore")
                    if search_regex.search(text):
                        # Extract relevant paragraphs
                        for line in text.splitlines():
                            line_str = line.strip()
                            if search_regex.search(line_str) and len(line_str) > 15:
                                vault_matches.append(f"• [Hafıza Dosyası: {md_file.stem}] {line_str[:180]}")
                                if len(vault_matches) >= 5:
                                    break
                except Exception:
                    continue
        except Exception as e:
            logging.error("server_wiki vault search error: %s", e)

    if not results and not vault_matches:
        return f"Sunucu bilgi bankasında '{clean_query}' ile ilgili kayıtlı bir rehber, link veya bilgi bulunamadı."

    report = [
        f"📚 **SUNUCU BİLGİ BANKASI / WIKI BULGULARI:**",
        f"• Aranan Konu: \"{clean_query}\"",
        "",
    ]

    if vault_matches:
        report.append("🧠 **Hafıza Notları ve Özetler:**")
        report.extend(vault_matches)
        report.append("")

    if results:
        report.append("💬 **Geçmiş Sohbet Arşivinden Paylaşılanlar ve Linkler:**")
        report.extend(results)
        report.append("")

    report.append(
        "[Talimat: Bu bulguları derleyerek kullanıcının aradığı soruya, rehbere, ayara veya linke doğrudan, "
        "kapsamlı ve derli toplu bir yanıt ver. Kimin ne zaman paylaştığını belirt. "
        "ASLA VİRGÜL KULLANMA. Bilgili ve samimi bir üslup kullan.]"
    )

    return "\n".join(report)


SERVER_WIKI_SCHEMA = {
    "type": "function",
    "function": {
        "name": "server_wiki",
        "description": "Look up shared guides, recommendations, settings, links, and collected server knowledge from the long-term memory vault and archive. Use when asked 'Onur'un paylaştığı ayar neydi', 'şu konuda tavsiye', 'geçmişteki link', or searching server knowledge.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The topic, setting, guide, recommendation, or keyword to retrieve from the server knowledge base",
                },
                "category": {
                    "type": "string",
                    "description": "Optional category filter: 'link', 'guide', or 'all'",
                    "default": "all",
                },
            },
            "required": ["query"],
        },
    },
}

TOOL_NAME = "server_wiki"
TOOL_ENTRY = ToolEntry(
    schema=SERVER_WIKI_SCHEMA,
    fn=server_wiki,
)
