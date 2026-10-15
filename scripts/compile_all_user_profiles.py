#!/usr/bin/env python3
"""
Pre-compiler for Discord Server User Profiles and Personality Dossiers.
Extracts, structures, and indexes behavioral & psychological profiles
for all active members in the server into:
1. SQLite table `user_profiles` and `user_profile_links` in archive.sqlite3
2. Markdown notes in /var/lib/discord-ai-memory/vault/profiles/<user>.md

Allows the Discord bot and Basic Memory to instantly recall deep personality
analyses, quotes, and habits without re-scanning 90,000+ messages on the fly.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("compile_profiles")

MEMORY_DIR = Path(os.environ.get("DISCORD_MEMORY_DIR", "/var/lib/discord-ai-memory"))
ARCHIVE_PATH = MEMORY_DIR / "archive.sqlite3"
PROFILES_VAULT_DIR = MEMORY_DIR / "vault" / "profiles"

KNOWN_CHANNELS = {
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

# Known multi-account clusters
KNOWN_CLUSTERS = {
    "Apro": [405703268753211393],
    "sez": [962397030544707695, 809050354133237760],
    "yakup": [1403857873292759161, 456226577798135808],
    "19": [455020808939765770],
    "onur": [630153541465538561, 1498684197370597438, 1475228353211666595, 301044320251805697, 963848559176847370],
    "yiğit": [697793272873222295],
    "mworty": [556461336062590987],
    "Lydiaq": [594192960380469252],
    "Nil": [999001],
}

USER_METADATA = {
    "apro": {
        "title": "APRO (TALHA SEMİH GÜNDÜZ)",
        "real_name": "Talha Semih Gündüz",
        "notes": "Sunucuda herkes ona Apro veya Talha der.",
        "aliases": ["apro", "talha", "semih", "talha semih", "talha semih gündüz", "talha gündüz"],
    },
    "sez": {
        "title": "SEZ (SEZİN BOYRAZ)",
        "real_name": "Sezin Boyraz",
        "notes": "Kızdır (kadındır). Ona hitap ederken ve bahsederken kadın ifadeleri/zamirleri kullanılır.",
        "aliases": ["sez", "sezin", "sezin boyraz", "sezrage", "kürtçük"],
    },
    "yakup": {
        "title": "YAKUP (YAKUP AVŞAR)",
        "real_name": "Yakup Avşar",
        "notes": "Ebrar Avşar (Lydiaq) ile kardeştirler. Eski hesabı 'Deleted User', yeni hesabı 'jambenoire'dir.",
        "aliases": ["yakup", "yakup avşar", "jambenoire", "deleted user", "yakub", "salgamli meni"],
    },
    "19": {
        "title": "19 (ALİ ARDA BAKKAL)",
        "real_name": "Ali Arda Bakkal",
        "notes": "Discord kullanıcı adı '19', sunucuda herkes ona 'Arda' der.",
        "aliases": ["19", "arda", "ali arda", "ali arda bakkal", "arda bakkal"],
    },
    "onur": {
        "title": "ONUR",
        "real_name": "Onur",
        "notes": "Sunucu Yöneticisi. 'onur', 'inactive' ve 'amyalamak' hesaplarının tamamı Onur'a aittir.",
        "aliases": ["onur", "benonur", "witch", "amyalamak", "inactive"],
    },
    "yiğit": {
        "title": "YİĞİT (YİĞİT YİRMİBEŞ)",
        "real_name": "Yiğit Yirmibeş",
        "notes": "Gerçek adı Yiğit Yirmibeş.",
        "aliases": ["yiğit", "yigit", "yiğit yirmibeş", "yigit yirmibes"],
    },
    "mworty": {
        "title": "MWORTY (MUHAMMET UMUTLU)",
        "real_name": "Muhammet Umutlu",
        "notes": "Discord kullanıcı adı 'mworty', gerçek adı Muhammet Umutlu.",
        "aliases": ["mworty", "muhammet", "muhammed", "muhammet umutlu"],
    },
    "lydiaq": {
        "title": "LYDIAQ (EBRAR AVŞAR)",
        "real_name": "Ebrar Avşar",
        "notes": "Kızdır (kadındır). Ona hitap ederken ve bahsederken kadın ifadeleri/zamirleri kullanılır. Yakup Avşar'ın kız kardeşidir.",
        "aliases": ["lydiaq", "ebrar", "ebrar avşar", "lydia"],
    },
    "nil": {
        "title": "CRIMSON MOON (NİL)",
        "real_name": "Nil",
        "notes": "Kızdır (kadındır). Ona hitap ederken ve bahsederken kadın ifadeleri/zamirleri kullanılır. Discord kullanıcı adı 'Crimson Moon'.",
        "aliases": ["nil", "crimson moon", "crimson", "crimsonmoon"],
    },
}

CLUSTER_EXTRA_ALIASES = {
    cname: meta["aliases"] for cname, meta in USER_METADATA.items()
}


def init_db(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            author_id INTEGER PRIMARY KEY,
            canonical_name TEXT,
            all_author_ids TEXT,
            total_messages INTEGER,
            first_date TEXT,
            last_date TEXT,
            night_pct REAL,
            top_channels TEXT,
            top_mentions TEXT,
            profile_dossier TEXT,
            updated_at TEXT
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_user_profiles_name ON user_profiles(canonical_name)")
    db.execute("DROP TABLE IF EXISTS user_profile_links")
    db.execute("""
        CREATE TABLE user_profile_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER,
            canonical_author_id INTEGER,
            name TEXT,
            UNIQUE(author_id, name)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_profile_links_canon ON user_profile_links(canonical_author_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_profile_links_author ON user_profile_links(author_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_profile_links_name ON user_profile_links(name)")
    db.commit()


def resolve_channel(cid: int) -> str:
    return KNOWN_CHANNELS.get(cid, f"#{cid}")


def build_user_dossier(db: sqlite3.Connection, canonical_name: str, author_ids: list[int]) -> tuple[dict, str]:
    placeholders = ",".join("?" * len(author_ids))

    # 1. Total & dates
    count_row = db.execute(
        f"SELECT count(*), min(created), max(created) FROM messages WHERE author IN ({placeholders})",
        author_ids,
    ).fetchone()

    total_msgs = count_row[0]
    first_date = str(count_row[1])[:10] if count_row[1] else "Bilinmiyor"
    last_date = str(count_row[2])[:10] if count_row[2] else "Bilinmiyor"

    # Accounts breakdown
    acc_rows = db.execute(
        f"SELECT author, name, count(*) as cnt FROM messages WHERE author IN ({placeholders}) GROUP BY author, name ORDER BY cnt DESC",
        author_ids,
    ).fetchall()
    acc_summary = ", ".join(f"{r[1]} ({r[0]}): {r[2]} mesaj" for r in acc_rows)

    # 2. Time-of-day
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

    # 3. Channels
    top_chans = db.execute(
        f"SELECT channel, count(*) as cnt FROM messages WHERE author IN ({placeholders}) GROUP BY channel ORDER BY cnt DESC LIMIT 4",
        author_ids,
    ).fetchall()
    top_channels_data = [{"channel": resolve_channel(r[0]), "id": r[0], "count": r[1]} for r in top_chans]
    chan_summary = ", ".join(f"{c['channel']} ({c['count']})" for c in top_channels_data)

    # 4. Social mentions
    mention_rows = db.execute(
        f"SELECT content FROM messages WHERE author IN ({placeholders}) AND content LIKE '%<@%' LIMIT 3000",
        author_ids,
    ).fetchall()
    mentions: list[int] = []
    for r in mention_rows:
        for m in re.findall(r"<@!?([0-9]+)>", r[0]):
            mentions.append(int(m))
    top_mentions_raw = Counter(mentions).most_common(5)
    top_mentions_data = []
    mention_parts = []
    for uid, cnt in top_mentions_raw:
        nrow = db.execute("SELECT name FROM messages WHERE author = ? LIMIT 1", (uid,)).fetchone()
        uname = nrow[0] if nrow else str(uid)
        top_mentions_data.append({"user_id": uid, "name": uname, "count": cnt})
        mention_parts.append(f"@{uname} ({cnt} etkileşim)")
    social_summary = ", ".join(mention_parts) or "Özel kullanıcı etiketi tespit edilmedi"

    # 5. Stratified Quotes
    # Category A: Long / Expressive / Thoughtful
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

    # Category B: Humor, Banter, Sarcasm & Jargon
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

    # Category C: Games, Tech, Hardware & Daily life
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

    # Category D: Chronological Samples
    early_rows = db.execute(
        f"""
        SELECT created, content FROM messages 
        WHERE author IN ({placeholders}) 
          AND strftime('%Y', created) IN ('2021', '2022', '2023')
          AND length(content) BETWEEN 25 AND 220 
          AND content NOT LIKE '%http%' AND content NOT LIKE '[%]%'
        ORDER BY random() LIMIT 5
        """,
        author_ids,
    ).fetchall()

    recent_rows = db.execute(
        f"""
        SELECT created, content FROM messages 
        WHERE author IN ({placeholders}) 
          AND strftime('%Y', created) IN ('2025', '2026')
          AND length(content) BETWEEN 25 AND 220 
          AND content NOT LIKE '%http%' AND content NOT LIKE '[%]%'
        ORDER BY random() LIMIT 5
        """,
        author_ids,
    ).fetchall()

    # Build dossier
    meta_info = USER_METADATA.get(canonical_name.lower())
    title = meta_info["title"] if meta_info else canonical_name.upper()

    lines = [
        f"╔═══════════════════════════════════════════════════════════════════════╗",
        f"║ DERİN KİŞİLİK VE DAVRANIŞ ARŞİV DOSYASI: {title}",
        f"╚═══════════════════════════════════════════════════════════════════════╝",
    ]
    if meta_info:
        lines.append(f"• Gerçek Adı: {meta_info['real_name']}")
        lines.append(f"• Kimlik / Kişi Notu: {meta_info['notes']}")
    lines.extend([
        f"• Toplam Arşivlenen Mesaj: {total_msgs:,} adet ({first_date} — {last_date} arası)",
        f"• Bağlı Hesaplar: {acc_summary}",
        f"• Zaman Dağılımı: Gece Kuşu: %{night_pct} | Akşam: %{evening_pct} | Gündüz: %{day_pct}",
        f"• En Aktif Kanallar: {chan_summary}",
        f"• En Yakın Çevre / En Çok Konuştuğu Kişiler: {social_summary}",
        "",
        "════════════════════ GERÇEK MESAJ KANITLARI & ALINTILAR ════════════════════",
        "【1. Düşünceler, Hayat, İçsel Paylaşımlar & Anlatımlar】",
    ])
    for r in deep_rows:
        lines.append(f'• [{str(r[0])[:10]}] "{r[1]}"')

    lines.append("\n【2. Mizah, Sarkazm, İroni & Dobralık】")
    for r in humor_rows:
        lines.append(f'• [{str(r[0])[:10]}] "{r[1]}"')

    lines.append("\n【3. Oyun, Teknoloji, Donanım & Günlük Hayat】")
    for r in tech_rows:
        lines.append(f'• [{str(r[0])[:10]}] "{r[1]}"')

    lines.append("\n【4. Zaman İçindeki Değişim & Son Ruh Hali】")
    if early_rows:
        lines.append("── İlk Dönem Mesajları:")
        for r in early_rows:
            lines.append(f'  • [{str(r[0])[:10]}] "{r[1]}"')
    if recent_rows:
        lines.append("── Güncel Dönem Mesajları:")
        for r in recent_rows:
            lines.append(f'  • [{str(r[0])[:10]}] "{r[1]}"')

    lines.extend([
        "",
        "════════════════════ LLM ANALİZ YÖNERGESİ (ZORUNLU) ════════════════════",
        f"Kullanıcıya yanıt verirken ASLA '{total_msgs} mesajın var, çok konuşuyorsun' gibi yüzeysel laflar etme.",
        "Yukarıdaki gerçek mesaj alıntılarını, tarihlerini ve konuşma üslubunu doğrudan referans alarak:",
        "1. İletişim Tarzı ve Mizahı (Sarkastik mi, dobra mı, nasıl dalga geçer, hangi kalıpları kullanır)",
        "2. İlgi Alanları ve Tutkuları (Oyun takıntıları, donanım/teknoloji, gündem takibi)",
        "3. Duygusal Dünyası ve Ruh Hali (Filtresiz içtenliği, dönem dönem yaşadığı mental yorgunluk veya hevesler)",
        "4. İnsan İlişkileri (Arkadaşlarına ve sunucuya yaklaşımı, kime takıldığı)",
        "başlıkları altında derin, gözlemci, esprili ve zeki bir kişilik portresi çiz.",
    ])

    dossier_text = "\n".join(lines)

    meta = {
        "author_id": author_ids[0],
        "canonical_name": canonical_name,
        "all_author_ids": json.dumps(author_ids),
        "total_messages": total_msgs,
        "first_date": first_date,
        "last_date": last_date,
        "night_pct": night_pct,
        "top_channels": json.dumps(top_channels_data),
        "top_mentions": json.dumps(top_mentions_data),
        "profile_dossier": dossier_text,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    return meta, dossier_text


def write_vault_profile(canonical_name: str, dossier_text: str) -> None:
    PROFILES_VAULT_DIR.mkdir(parents=True, exist_ok=True)
    # Sanitize filename
    safe_name = re.sub(r'[\/*?:"<>|]', "", canonical_name).strip() or "unnamed"
    file_path = PROFILES_VAULT_DIR / f"{safe_name}.md"
    file_path.write_text(dossier_text, encoding="utf-8")


def compile_all() -> None:
    if not ARCHIVE_PATH.exists():
        logger.error("Archive not found at %s", ARCHIVE_PATH)
        sys.exit(1)

    db = sqlite3.connect(ARCHIVE_PATH)
    init_db(db)

    # 1. Gather all authors and group them
    author_counts = db.execute("""
        SELECT author, name, count(*) as cnt 
        FROM messages 
        GROUP BY author, name 
        HAVING cnt >= 5
        ORDER BY cnt DESC
    """).fetchall()

    logger.info("Found %d author-name records with >= 5 messages.", len(author_counts))

    # Map name -> author_ids
    clusters: dict[str, list[int]] = {}
    name_map: dict[int, str] = {}
    assigned_authors: set[int] = set()

    # Pre-assign known clusters
    for canon_name, ids in KNOWN_CLUSTERS.items():
        clusters[canon_name] = list(ids)
        for aid in ids:
            assigned_authors.add(aid)
            name_map[aid] = canon_name

    # Assign remaining
    for aid, name, cnt in author_counts:
        if aid in assigned_authors:
            continue
        cname = name.strip() if name else f"User-{aid}"
        # Group identical lower names
        found_cluster = None
        for cl_name in clusters:
            if cl_name.lower() == cname.lower():
                found_cluster = cl_name
                break

        if found_cluster:
            clusters[found_cluster].append(aid)
        else:
            clusters[cname] = [aid]

        assigned_authors.add(aid)
        name_map[aid] = cname

    logger.info("Grouped into %d distinct member profiles.", len(clusters))

    # 2. Compile each profile
    compiled_count = 0
    for canon_name, aids in clusters.items():
        logger.info("Compiling profile for '%s' (IDs: %s)...", canon_name, aids)
        meta, dossier = build_user_dossier(db, canon_name, aids)

        # Upsert into user_profiles
        db.execute("""
            INSERT OR REPLACE INTO user_profiles (
                author_id, canonical_name, all_author_ids, total_messages,
                first_date, last_date, night_pct, top_channels, top_mentions,
                profile_dossier, updated_at
            ) VALUES (
                :author_id, :canonical_name, :all_author_ids, :total_messages,
                :first_date, :last_date, :night_pct, :top_channels, :top_mentions,
                :profile_dossier, :updated_at
            )
        """, meta)

        # Upsert into user_profile_links
        canon_id = meta["author_id"]
        for aid in aids:
            # fetch display names for this author
            names = db.execute("SELECT DISTINCT name FROM messages WHERE author = ?", (aid,)).fetchall()
            for (un,) in names:
                if un:
                    db.execute("""
                        INSERT OR IGNORE INTO user_profile_links (author_id, canonical_author_id, name)
                        VALUES (?, ?, ?)
                    """, (aid, canon_id, un.strip().lower()))
            db.execute("""
                INSERT OR IGNORE INTO user_profile_links (author_id, canonical_author_id, name)
                VALUES (?, ?, ?)
            """, (aid, canon_id, canon_name.strip().lower()))

        # Also register extra aliases and real names
        extra_aliases = CLUSTER_EXTRA_ALIASES.get(canon_name.lower(), [])
        for alias in extra_aliases:
            for aid in aids:
                db.execute("""
                    INSERT OR IGNORE INTO user_profile_links (author_id, canonical_author_id, name)
                    VALUES (?, ?, ?)
                """, (aid, canon_id, alias.strip().lower()))

        # Write to Vault
        try:
            write_vault_profile(canon_name, dossier)
            for alias in extra_aliases:
                if alias.lower() not in ("deleted user", "inactive", "amyalamak", canon_name.lower()):
                    write_vault_profile(alias, dossier)
        except Exception as e:
            logger.warning("Failed to write vault markdown for %s: %s", canon_name, e)

        compiled_count += 1

    db.commit()
    db.close()
    logger.info("Successfully compiled and indexed %d profiles into database and vault!", compiled_count)


if __name__ == "__main__":
    compile_all()
