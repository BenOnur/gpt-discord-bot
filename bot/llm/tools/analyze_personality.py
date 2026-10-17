"""
Deep Personality & Behavioral Profiling Tool.
Analyzes years of historical messages from the SQLite archive to reconstruct
a member's communication style, psychological tone, humor/sarcasm, habits,
night owl tendencies, and interests based on authentic dated quotes.
"""
from __future__ import annotations

from collections import Counter
import logging
import os
from pathlib import Path
import re
import sqlite3
from bot.archive_access import open_archive
from typing import Any

from bot.llm.tools._types import ToolEntry
from bot.llm.tools.discord_ctx import get_current_discord_context

_ARCHIVE_PATH = Path(os.getenv("DISCORD_MEMORY_DIR", "/var/lib/discord-ai-memory")) / "archive.sqlite3"


def _resolve_channel_name(channel_id: int, ctx: dict) -> str:
    bot = ctx.get("bot") if isinstance(ctx, dict) else None
    if bot:
        ch = bot.get_channel(channel_id)
        if ch and hasattr(ch, "name"):
            return f"#{ch.name}"
    # Known fallback channel map
    known_channels = {
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
    return known_channels.get(channel_id, f"Kanal-{channel_id}")


def analyze_personality(user_query: str = "") -> str:
    """
    Perform deep psychological and behavioral profiling on a Discord user
    by mining their entire historical message archive across all their accounts.
    """
    if not _ARCHIVE_PATH.exists():
        return "Sunucu mesaj arşivi henüz oluşturulmamış veya erişilemiyor."

    ctx = get_current_discord_context()
    author = ctx.get("author") if isinstance(ctx, dict) else None

    clean_q = user_query.strip().lstrip("@") if user_query else ""
    generic_self = ("ben", "beni", "kendim", "kişiliğim", "profilim", "benim", "me", "myself")

    target_id: int | None = None
    target_name: str | None = None

    if not clean_q or clean_q.lower() in generic_self:
        if author:
            target_id = author.id
            target_name = author.name
    elif clean_q.isdigit():
        target_id = int(clean_q)
    else:
        target_name = clean_q

    try:
        db = open_archive(_ARCHIVE_PATH)
        db.row_factory = sqlite3.Row

        # If we have target_id, find primary username
        if target_id and not target_name:
            r = db.execute("SELECT name FROM messages WHERE author = ? LIMIT 1", (target_id,)).fetchone()
            if r:
                target_name = r["name"]

        # Fast path: Check pre-compiled user_profiles cache
        try:
            lookup_val = (target_name or clean_q or "").lower()
            cached = db.execute("""
                SELECT up.profile_dossier
                FROM user_profiles up
                JOIN user_profile_links upl ON up.author_id = upl.canonical_author_id
                WHERE upl.author_id = ? OR lower(upl.name) = ? OR lower(up.canonical_name) = ?
                LIMIT 1
            """, (target_id, lookup_val, lookup_val)).fetchone()
            if cached and cached[0]:
                logging.info("analyze_personality: Returning pre-compiled brain dossier for %s (target_id=%s)", target_name, target_id)
                return cached[0]
        except sqlite3.OperationalError:
            pass  # Table might not exist yet if compiler hasn't run

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

        # If we have target_name, find all matching author IDs
        author_ids: list[int] = []
        if target_name:
            matches = db.execute(
                "SELECT DISTINCT author, name FROM messages WHERE lower(name) = ? OR name LIKE ?",
                (target_name.lower(), f"%{target_name}%"),
            ).fetchall()
            author_ids = [m["author"] for m in matches]

        if target_id and target_id not in author_ids:
            author_ids.append(target_id)

        # Expand known multi-account clusters
        for cl_key, cl_ids in KNOWN_CLUSTERS.items():
            if (target_name and cl_key in target_name.lower()) or (clean_q and cl_key in clean_q.lower()) or any(i in cl_ids for i in author_ids):
                for cid in cl_ids:
                    if cid not in author_ids:
                        author_ids.append(cid)

        if not author_ids:
            return f"'{user_query}' adına ait sunucu mesaj arşivinde hiçbir kayıt bulunamadı."

        placeholders = ",".join("?" * len(author_ids))

        # 1. Total count & date range
        count_row = db.execute(
            f"SELECT count(*), min(created), max(created) FROM messages WHERE author IN ({placeholders})",
            author_ids,
        ).fetchone()

        total_msgs = count_row[0]
        if total_msgs == 0:
            return f"Belirtilen kullanıcı için arşivde mesaj bulunamadı."

        first_date = str(count_row[1])[:10] if count_row[1] else "Bilinmiyor"
        last_date = str(count_row[2])[:10] if count_row[2] else "Bilinmiyor"

        # Account breakdown
        acc_rows = db.execute(
            f"SELECT author, name, count(*) as cnt FROM messages WHERE author IN ({placeholders}) GROUP BY author ORDER BY cnt DESC",
            author_ids,
        ).fetchall()
        acc_summary = ", ".join(f"{r['name']} ({r['author']}): {r['cnt']} mesaj" for r in acc_rows)

        # 2. Time-of-day habits (Night owl vs daytime)
        created_times = db.execute(
            f"SELECT created FROM messages WHERE author IN ({placeholders}) AND created IS NOT NULL",
            author_ids,
        ).fetchall()

        hours = [int(r[0][11:13]) for r in created_times if len(r[0]) >= 13 and r[0][11:13].isdigit()]
        hour_counts = Counter(hours)
        total_valid_hours = len(hours) or 1
        night_cnt = sum(hour_counts[h] for h in range(1, 6))
        morning_cnt = sum(hour_counts[h] for h in range(6, 12))
        afternoon_cnt = sum(hour_counts[h] for h in range(12, 18))
        evening_cnt = sum(hour_counts[h] for h in [18, 19, 20, 21, 22, 23, 0])

        night_pct = round(night_cnt / total_valid_hours * 100, 1)
        day_pct = round((morning_cnt + afternoon_cnt) / total_valid_hours * 100, 1)
        evening_pct = round(evening_cnt / total_valid_hours * 100, 1)

        # 3. Top channels
        top_chan_rows = db.execute(
            f"SELECT channel, count(*) as cnt FROM messages WHERE author IN ({placeholders}) GROUP BY channel ORDER BY cnt DESC LIMIT 4",
            author_ids,
        ).fetchall()
        chan_summary = ", ".join(f"{_resolve_channel_name(r['channel'], ctx)} ({r['cnt']})" for r in top_chan_rows)

        # 4. Social graph: Who does this user tag/interact with most?
        mention_rows = db.execute(
            f"SELECT content FROM messages WHERE author IN ({placeholders}) AND content LIKE '%<@%' LIMIT 3000",
            author_ids,
        ).fetchall()
        mentions: list[int] = []
        for r in mention_rows:
            for m in re.findall(r"<@!?([0-9]+)>", r["content"]):
                mentions.append(int(m))
        top_mentions = Counter(mentions).most_common(5)
        mention_summary_parts = []
        for uid, cnt in top_mentions:
            name_row = db.execute("SELECT name FROM messages WHERE author = ? LIMIT 1", (uid,)).fetchone()
            uname = name_row[0] if name_row else str(uid)
            mention_summary_parts.append(f"@{uname} ({cnt} etkileşim)")
        social_summary = ", ".join(mention_summary_parts) or "Özel kullanıcı etiketi tespit edilmedi"

        # 5. Stratified Qualitative Message Extraction (The Heart of the Analysis)
        # Category A: Long / Expressive / Thoughtful / Philosophical / Life rants
        deep_rows = db.execute(
            f"""
            SELECT created, content FROM messages 
            WHERE author IN ({placeholders}) 
              AND length(content) BETWEEN 40 AND 320 
              AND content NOT LIKE '%http%' 
              AND content NOT LIKE '[%]%'
            ORDER BY random() LIMIT 12
            """,
            author_ids,
        ).fetchall()

        # Category B: Humor, Banter, Sarcasm & Internet Culture
        humor_rows = db.execute(
            f"""
            SELECT created, content FROM messages 
            WHERE author IN ({placeholders}) 
              AND (
                content LIKE '%amk%' OR content LIKE '%ahaha%' OR content LIKE '%asdf%' 
                OR content LIKE '%dostum%' OR content LIKE '%lan%' OR content LIKE '%olm%'
                OR content LIKE '%troll%' OR content LIKE '%gerizekalı%'
              )
              AND length(content) BETWEEN 25 AND 200 
              AND content NOT LIKE '%http%' 
              AND content NOT LIKE '[%]%'
            ORDER BY random() LIMIT 8
            """,
            author_ids,
        ).fetchall()

        # Category C: Games, Tech, Hardware & Daily Observations
        tech_rows = db.execute(
            f"""
            SELECT created, content FROM messages 
            WHERE author IN ({placeholders}) 
              AND (
                content LIKE '%oyun%' OR content LIKE '%steam%' OR content LIKE '%fps%' 
                OR content LIKE '%pc%' OR content LIKE '%ekran%' OR content LIKE '%save%'
                OR content LIKE '%para%' OR content LIKE '%iş%' OR content LIKE '%dolar%'
              )
              AND length(content) BETWEEN 25 AND 220 
              AND content NOT LIKE '%http%' 
              AND content NOT LIKE '[%]%'
            ORDER BY random() LIMIT 8
            """,
            author_ids,
        ).fetchall()

        # Category D: Chronological Evolution (2023 Early vs 2025/2026 Late)
        early_rows = db.execute(
            f"""
            SELECT created, content FROM messages 
            WHERE author IN ({placeholders}) 
              AND strftime('%Y', created) IN ('2023')
              AND length(content) BETWEEN 30 AND 220 
              AND content NOT LIKE '%http%' AND content NOT LIKE '[%]%'
            ORDER BY random() LIMIT 4
            """,
            author_ids,
        ).fetchall()

        recent_rows = db.execute(
            f"""
            SELECT created, content FROM messages 
            WHERE author IN ({placeholders}) 
              AND strftime('%Y', created) IN ('2025', '2026')
              AND length(content) BETWEEN 30 AND 220 
              AND content NOT LIKE '%http%' AND content NOT LIKE '[%]%'
            ORDER BY random() LIMIT 5
            """,
            author_ids,
        ).fetchall()

        # Build comprehensive dossier
        out = [
            f"╔═══════════════════════════════════════════════════════════════════════╗",
            f"║ DERİN KİŞİLİK VE DAVRANIŞ ARŞİV DOSYASI: {target_name.upper()}",
            f"╚═══════════════════════════════════════════════════════════════════════╝",
            f"• Toplam Arşivlenen Mesaj: {total_msgs:,} adet ({first_date} — {last_date} arası)",
            f"• Bağlı Hesaplar: {acc_summary}",
            f"• Zaman Dağılımı: Gece Kuşu: %{night_pct} | Akşam: %{evening_pct} | Gündüz: %{day_pct}",
            f"• En Aktif Kanallar: {chan_summary}",
            f"• En Yakın Çevre / En Çok Konuştuğu Kişiler: {social_summary}",
            "",
            "════════════════════ GERÇEK MESAJ KANITLARI & ALINTILAR ════════════════════",
            "【1. Düşünceler, Hayat, İçsel Paylaşımlar & Anlatımlar】",
        ]
        for r in deep_rows:
            out.append(f'• [{str(r["created"])[:10]}] "{r["content"]}"')

        out.append("\n【2. Mizah, Sarkazm, İroni & Dobralık】")
        for r in humor_rows:
            out.append(f'• [{str(r["created"])[:10]}] "{r["content"]}"')

        out.append("\n【3. Oyun, Teknoloji, Donanım & Günlük Hayat】")
        for r in tech_rows:
            out.append(f'• [{str(r["created"])[:10]}] "{r["content"]}"')

        out.append("\n【4. Zaman İçindeki Değişim & Son Ruh Hali】")
        out.append("── 2023 Erken Dönem:")
        for r in early_rows:
            out.append(f'  • [{str(r["created"])[:10]}] "{r["content"]}"')
        out.append("── 2025/2026 Güncel Dönem:")
        for r in recent_rows:
            out.append(f'  • [{str(r["created"])[:10]}] "{r["content"]}"')

        out.extend([
            "",
            "════════════════════ LLM ANALİZ YÖNERGESİ (ZORUNLU) ════════════════════",
            "Kullanıcıya yanıt verirken ASLA '7000 mesajın var, çok konuşuyorsun' gibi yüzeysel laflar etme.",
            "Yukarıdaki gerçek mesaj alıntılarını, tarihlerini ve konuşma üslubunu doğrudan referans alarak:",
            "1. İletişim Tarzı ve Mizahı (Sarkastik mi, dobra mı, nasıl dalga geçer, hangi kalıpları kullanır)",
            "2. İlgi Alanları ve Tutkuları (Oyun takıntıları, donanım/teknoloji, gündem takibi)",
            "3. Duygusal Dünyası ve Ruh Hali (Filtresiz içtenliği, dönem dönem yaşadığı mental yorgunluk veya hevesler)",
            "4. İnsan İlişkileri (Arkadaşlarına ve sunucuya yaklaşımı, kime takıldığı)",
            "başlıkları altında derin, gözlemci, esprili ve zeki bir kişilik portresi çiz.",
        ])

        return "\n".join(out)

    except Exception as e:
        logging.error("analyze_personality error: %s", e)
        return f"Kişilik analizi verisi çıkarılırken hata oluştu: {e}"


PERSONALITY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "analyze_personality",
        "description": "Deep psychological, behavioral, and personality profiling tool based on historical Discord messages. ALWAYS call this tool when the user asks 'kişiliğimi özetle', 'beni analiz et', 'karakterim nasıl', 'psikolojik profilimi çıkar', 'mesajlarıma göre ben nasıl biriyim', or to understand someone's real personality from their message archive.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_query": {
                    "type": "string",
                    "description": "Username, display name, Discord ID, or empty/'ben' to analyze the current speaker.",
                }
            },
            "required": [],
        },
    },
}

TOOL_NAME = "analyze_personality"
TOOL_ENTRY = ToolEntry(
    schema=PERSONALITY_SCHEMA,
    fn=analyze_personality,
)
