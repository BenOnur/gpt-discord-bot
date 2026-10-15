"""
Personalized Catch-Up Briefing Tool ('Ben Yokken Ne Oldu? / FOMO Katili').
Analyzes what happened on the server while the user was away:
- Mentions and direct references to the user
- Inside jokes, major topics, and server events since user's last activity
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any

from bot.llm.tools._types import ToolEntry
from bot.llm.tools.discord_ctx import get_current_discord_context

_ARCHIVE_PATH = Path(os.getenv("DISCORD_MEMORY_DIR", "/var/lib/discord-ai-memory")) / "archive.sqlite3"


def catch_me_up(target_user: str | None = None, hours: int | None = None) -> str:
    """
    Generate a personalized catch-up briefing for a user showing what they missed.
    """
    if not _ARCHIVE_PATH.exists():
        return "Sunucu mesaj arşivi henüz oluşturulmamış veya erişilemiyor."

    ctx = get_current_discord_context()
    author = ctx.get("author") if isinstance(ctx, dict) else None

    # Resolve target user
    user_name = target_user.strip().lstrip("@") if target_user else ""
    user_id = None

    if not user_name and author:
        user_name = getattr(author, "display_name", author.name)
        user_id = author.id

    try:
        db = sqlite3.connect(_ARCHIVE_PATH, timeout=5)
        db.row_factory = sqlite3.Row

        # If user_id not resolved yet, attempt to find user by name
        if not user_id and user_name:
            if user_name.isdigit():
                user_id = int(user_name)
            else:
                user_row = db.execute(
                    "SELECT author, name FROM messages WHERE name LIKE ? ORDER BY id DESC LIMIT 1",
                    (f"%{user_name}%",),
                ).fetchone()
                if user_row:
                    user_id = user_row["author"]
                    user_name = user_row["name"]

        # Determine time window: if hours not specified, find user's last message time
        window_hours = hours
        if not window_hours:
            if user_id:
                last_msg_row = db.execute(
                    "SELECT max(created) as last_seen FROM messages WHERE author = ?",
                    (user_id,),
                ).fetchone()
                if last_msg_row and last_msg_row["last_seen"]:
                    try:
                        last_seen_dt = datetime.fromisoformat(str(last_msg_row["last_seen"]))
                        if last_seen_dt.tzinfo is None:
                            last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
                        delta = datetime.now(timezone.utc) - last_seen_dt
                        window_hours = max(2, min(72, int(delta.total_seconds() / 3600)))
                    except Exception:
                        window_hours = 24
            if not window_hours:
                window_hours = 24

        window_hours = max(1, min(168, window_hours))
        since_time = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

        # 1. Check messages mentioning or talking about this user
        direct_mentions = []
        mention_patterns = [f"%<@{user_id}>%"] if user_id else []
        if user_name and len(user_name) >= 3:
            mention_patterns.append(f"%{user_name}%")

        if mention_patterns:
            query_conds = " OR ".join(["content LIKE ?"] * len(mention_patterns))
            mention_rows = db.execute(
                f"""SELECT name, author, content, created, channel
                    FROM messages
                    WHERE created >= ? AND ({query_conds}) AND (author != ?)
                    ORDER BY id DESC LIMIT 20""",
                (since_time, *mention_patterns, user_id or 0),
            ).fetchall()
            for r in mention_rows:
                date_str = str(r["created"])[:16]
                clean_c = r["content"].replace("\n", " ")[:180]
                direct_mentions.append(f"• **{r['name']}** [{date_str}]: \"{clean_c}\"")

        # 2. General server highlights and discussions during the absence
        general_rows = db.execute(
            """SELECT name, author, content, created, channel
               FROM messages
               WHERE created >= ?
               ORDER BY id DESC LIMIT 200""",
            (since_time,),
        ).fetchall()

        if not general_rows:
            return f"Son {window_hours} saat içinde sunucuda henüz yeni bir mesaj akışı olmadı."

        authors = [r["name"] for r in general_rows if r["name"]]
        top_talkers = Counter(authors).most_common(4)

        # Pick key conversational moments
        meaningful_msgs = [
            r for r in general_rows
            if len(r["content"]) > 35 and not r["content"].startswith("http") and (not user_id or r["author"] != user_id)
        ][:8]

        report = [
            f"📋 **KİŞİSEL BRİFİNG RAPORU (Kullanıcı: {user_name or 'Bilinmiyor'})**",
            f"• İncelenen Zaman Dilimi: Son {window_hours} saat",
            f"• Bu sürede toplam konuşulan mesaj: {len(general_rows)}",
            f"• En aktif kişiler: {', '.join(f'{name} ({cnt})' for name, cnt in top_talkers)}",
            "",
        ]

        if direct_mentions:
            report.append("🎯 **SENİNLE İLGİLİ KONUŞULANLAR / ETİKETLER:**")
            report.extend(direct_mentions)
            report.append("")
        else:
            report.append("🎯 **SENİNLE İLGİLİ:** Yokluğunda adının geçtiği belirgin bir konu veya etiket yakalanmadı.")
            report.append("")

        if meaningful_msgs:
            report.append("💬 **GENEL SUNUCUDA DÖNEN MUHABBETLER:**")
            for m in meaningful_msgs:
                clean_txt = m["content"].replace("\n", " ")[:160]
                report.append(f"• **{m['name']}**: \"{clean_txt}\"")
            report.append("")

        report.append(
            "[Talimat: Bu verileri temel alarak doğrudan bu kullanıcıya hitaben 'Sen yokken bunlar oldu' "
            "tarzında samimi, net, virgülsüz, Discord jargonunda kişisel bir özet çıkar. "
            "Varsa onun hakkında konuşulanları mutlaka öne çıkar, ardından sunucudaki genel olayları aktar.]"
        )

        return "\n".join(report)

    except Exception as e:
        logging.error("catch_me_up error: %s", e)
        return f"Brifing hazırlanırken hata oluştu: {e}"


CATCH_ME_UP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "catch_me_up",
        "description": "Generate a personalized 'What did I miss?' briefing for the user or a target member. Analyzes what happened on the server since they were last seen, including direct mentions, inside gossip, and main discussions.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_user": {
                    "type": "string",
                    "description": "Optional username to target. If omitted, targets the user who invoked the command.",
                },
                "hours": {
                    "type": "integer",
                    "description": "Optional number of hours back to analyze (default: auto-detected based on user's last activity, max 168).",
                },
            },
        },
    },
}

TOOL_NAME = "catch_me_up"
TOOL_ENTRY = ToolEntry(
    schema=CATCH_ME_UP_SCHEMA,
    fn=catch_me_up,
)
