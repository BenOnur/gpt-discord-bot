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
from bot.archive_access import open_archive
from typing import Any

from bot.llm.tools._types import ToolEntry

from bot.llm.tools.discord_ctx import get_current_discord_context

_ARCHIVE_PATH = Path(os.getenv("DISCORD_MEMORY_DIR", "/var/lib/discord-ai-memory")) / "archive.sqlite3"


def _resolve_channel(channel_id: int) -> str:
    ctx = get_current_discord_context()
    bot = ctx.get("bot") if isinstance(ctx, dict) else None
    if bot:
        ch = bot.get_channel(channel_id)
        if ch and hasattr(ch, "name"):
            return f"#{ch.name}"
    known = {
        864872157565550636: "#çet",
        922804883818700820: "#owo",
        864888563278938152: "#müzik",
        864890800516759592: "#komut",
        917160904339103794: "#foto-video",
        867751597139820554: "#link",
        864890481507041290: "#yt-link",
        876223689912385607: "#meme-yığma-kanalı",
        880088057922089060: "#zenci-atma-kanalı",
        987117057982222397: "#kli̇p",
        864876278174842890: "#kurallar",
        926793205578629220: "#playlist",
        983437129747169280: "#ani̇me",
        1324606827908042762: "#i̇zlemek-zorunda-oldugunuz-di̇zi̇ler",
        1278090241953239121: "#oyun",
        1267914224638623814: "#hentaiodasi",
    }
    return known.get(channel_id, f"#{channel_id}")


def get_user_profile(user_query: str) -> str:
    """Analyze a server member's activity, speaking habits, and recent quotes."""
    clean_query = user_query.strip().lstrip("@")
    ctx = get_current_discord_context()
    author = ctx.get("author") if isinstance(ctx, dict) else None

    generic_self = ("ben", "beni", "kendim", "kişiliğim", "profilim", "benim", "me")
    target_id: int | None = None
    target_name: str | None = None

    if not clean_query or clean_query.lower() in generic_self:
        if author:
            target_id = author.id
            target_name = author.name
    elif clean_query.isdigit():
        target_id = int(clean_query)
    else:
        target_name = clean_query

    if not _ARCHIVE_PATH.exists():
        return "Sunucu mesaj arşivi henüz oluşturulmamış veya erişilemiyor."

    try:
        db = open_archive(_ARCHIVE_PATH)
        db.row_factory = sqlite3.Row

        if target_id and not target_name:
            r = db.execute("SELECT name FROM messages WHERE author = ? LIMIT 1", (target_id,)).fetchone()
            if r:
                target_name = r["name"]

        # Fast path: check pre-compiled cache
        try:
            lookup_val = (target_name or clean_query or "").lower()
            cached = db.execute("""
                SELECT up.profile_dossier
                FROM user_profiles up
                JOIN user_profile_links upl ON up.author_id = upl.canonical_author_id
                WHERE upl.author_id = ? OR lower(upl.name) = ? OR lower(up.canonical_name) = ?
                LIMIT 1
            """, (target_id, lookup_val, lookup_val)).fetchone()
            if cached and cached[0]:
                return cached[0]
        except sqlite3.OperationalError:
            pass

        KNOWN_CLUSTERS = {
            "onur": [630153541465538561, 1498684197370597438, 1475228353211666595, 301044320251805697, 963848559176847370],
            "sez": [962397030544707695, 809050354133237760],
            "yakup": [1403857873292759161, 456226577798135808],
            "jambenoire": [1403857873292759161, 456226577798135808],
            "arda": [455020808939765770],
            "19": [455020808939765770],
            "apro": [405703268753211393],
            "talha": [405703268753211393],
            "yiğit": [697793272873222295],
            "yigit": [697793272873222295],
            "mworty": [556461336062590987],
            "muhammet": [556461336062590987],
            "lydiaq": [594192960380469252],
            "ebrar": [594192960380469252],
            "nil": [999001],
            "crimson": [999001],
            "crimson moon": [999001],
        }

        author_ids: list[int] = []
        if target_name:
            matches = db.execute(
                "SELECT DISTINCT author, name FROM messages WHERE lower(name) = ? OR name LIKE ?",
                (target_name.lower(), f"%{target_name}%"),
            ).fetchall()
            author_ids = [m["author"] for m in matches]

        if target_id and target_id not in author_ids:
            author_ids.append(target_id)

        for cl_key, cl_ids in KNOWN_CLUSTERS.items():
            if (target_name and cl_key in target_name.lower()) or (clean_query and cl_key in clean_query.lower()) or any(i in cl_ids for i in author_ids):
                for cid in cl_ids:
                    if cid not in author_ids:
                        author_ids.append(cid)

        if not author_ids:
            return f"'{clean_query}' adına ait sunucu arşivinde mesaj bulunamadı."

        placeholders = ",".join("?" * len(author_ids))

        count_row = db.execute(
            f"SELECT count(*), min(created), max(created) FROM messages WHERE author IN ({placeholders})",
            author_ids,
        ).fetchone()

        if not count_row or count_row[0] == 0:
            return f"'{clean_query}' adına ait sunucu arşivinde mesaj bulunamadı."

        total_msgs = count_row[0]
        first_date = str(count_row[1])[:10] if count_row[1] else "Bilinmiyor"
        last_date = str(count_row[2])[:10] if count_row[2] else "Bilinmiyor"
        display_name = target_name or clean_query

        # Channel activity distribution
        top_channels = db.execute(
            f"SELECT channel, count(*) as cnt FROM messages WHERE author IN ({placeholders}) GROUP BY channel ORDER BY cnt DESC LIMIT 4",
            author_ids,
        ).fetchall()

        # Representative sample quotes across history (both recent and random memorable)
        recent_rows = db.execute(
            f"SELECT * FROM messages WHERE author IN ({placeholders}) ORDER BY id DESC LIMIT 15",
            author_ids,
        ).fetchall()

        random_rows = db.execute(
            f"""
            SELECT * FROM messages 
            WHERE author IN ({placeholders}) 
              AND length(content) BETWEEN 30 AND 200 
              AND content NOT LIKE '%http%' 
              AND content NOT LIKE '[%]%'
            ORDER BY random() LIMIT 15
            """,
            author_ids,
        ).fetchall()

        seen = set()
        sample_quotes = []
        for r in list(recent_rows) + list(random_rows):
            content = r["content"].strip()
            if len(content) > 15 and not content.startswith(("http://", "https://", "[")):
                if content not in seen:
                    seen.add(content)
                    sample_quotes.append(f'• [{str(r["created"])[:10]}] "{content}"')
            if len(sample_quotes) >= 12:
                break

        channel_parts = [f"{_resolve_channel(c['channel'])} ({c['cnt']})" for c in top_channels]
        top_chan_str = ", ".join(channel_parts)

        lines = [
            f"👤 Sunucu Üye Profili: {display_name} (Hesap Sayısı: {len(author_ids)}, ID'ler: {', '.join(str(i) for i in author_ids)})",
            f"• Toplam Arşivlenen Mesaj: {total_msgs:,} adet",
            f"• İlk Görülme: {first_date} | Son Görülme: {last_date}",
            f"• En Aktif Kanallar: {top_chan_str}",
            "",
            "Karakteristik & Hafızadaki Gerçek Mesaj Alıntıları:",
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
