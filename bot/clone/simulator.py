"""
AI Personality Simulator (Kişilik Klonu).
Mines a member's historical messages from SQLite to construct an authentic
persona prompt, and uses the LLM to generate replies mimicking that exact user.
"""
from __future__ import annotations

from collections import Counter
import logging
from pathlib import Path
import re
import sqlite3
from bot.archive_access import open_archive
from bot.llm.quota import completion
from typing import Any, Callable

import discord

logger = logging.getLogger(__name__)

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
}


def get_user_training_data(
    user_id: int,
    user_name: str,
    data_dir: str = "/var/lib/discord-ai-memory",
) -> tuple[list[str], list[str], str | None]:
    """
    Fetches sample messages, frequent keywords, and compiled dossier from SQLite archive.
    Returns: (sample_messages, frequent_words, dossier_summary)
    """
    db_file = Path(data_dir) / "archive.sqlite3"
    if not db_file.exists():
        return [], [], None

    conn = open_archive(db_file)
    conn.row_factory = sqlite3.Row
    try:
        # 1. Expand author IDs via known clusters and name matching
        author_ids = [user_id]
        name_lower = (user_name or "").lower()

        # Find matching accounts in messages table
        if name_lower:
            rows = conn.execute(
                "SELECT DISTINCT author FROM messages WHERE lower(name) = ? OR name LIKE ?",
                (name_lower, f"%{name_lower}%"),
            ).fetchall()
            for r in rows:
                if r["author"] and r["author"] not in author_ids:
                    author_ids.append(r["author"])

        # Check multi-account cluster map
        for cl_key, cl_ids in KNOWN_CLUSTERS.items():
            if cl_key in name_lower or any(cid in cl_ids for cid in author_ids):
                for cid in cl_ids:
                    if cid not in author_ids:
                        author_ids.append(cid)

        # 2. Check compiled profile dossier
        dossier: str | None = None
        try:
            placeholders = ",".join("?" for _ in author_ids)
            row = conn.execute(f"""
                SELECT up.profile_dossier
                FROM user_profiles up
                JOIN user_profile_links upl ON up.author_id = upl.canonical_author_id
                WHERE upl.author_id IN ({placeholders})
                LIMIT 1
            """, author_ids).fetchone()
            if row and row[0]:
                dossier = str(row[0])[:1200]
        except Exception:
            pass

        # 3. Retrieve recent and distinct historical messages (filtering commands and links)
        placeholders = ",".join("?" for _ in author_ids)
        query = f"""
            SELECT content FROM messages
            WHERE author IN ({placeholders})
              AND length(content) >= 5
              AND length(content) <= 280
              AND content NOT LIKE '/%'
              AND content NOT LIKE '!%'
              AND content NOT LIKE 'http%'
              AND content NOT LIKE '<@%'
            ORDER BY id DESC
            LIMIT 250
        """
        rows = conn.execute(query, author_ids).fetchall()
        raw_msgs = [r["content"].strip() for r in rows if r["content"]]

        # Clean messages
        clean_msgs = []
        seen = set()
        for msg in raw_msgs:
            # strip mentions
            clean = re.sub(r"<@!?[0-9]+>", "", msg).strip()
            if len(clean) >= 4 and clean.lower() not in seen:
                seen.add(clean.lower())
                clean_msgs.append(clean)
            if len(clean_msgs) >= 45:
                break

        # 4. Extract frequent slang / keywords
        words = []
        for msg in clean_msgs:
            for w in re.findall(r"\b[a-zA-ZçğıöşüÇĞİÖŞÜ]{4,15}\b", msg.lower()):
                if w not in {"bence", "çünkü", "kadar", "zaten", "sonra", "olarak", "böyle", "şöyle", "mesaj"}:
                    words.append(w)
        counts = Counter(words)
        top_words = [word for word, count in counts.most_common(12) if count >= 2]

        return clean_msgs, top_words, dossier
    finally:
        conn.close()


def build_impersonation_prompt(
    user_name: str,
    display_name: str,
    sample_messages: list[str],
    top_words: list[str],
    dossier: str | None,
) -> str:
    """Builds the comprehensive system prompt for the AI clone."""
    samples_text = "\n".join(f"- “{m}”" for m in sample_messages[:35]) if sample_messages else "- (Örnek mesaj bulunamadı)"
    words_text = ", ".join(top_words) if top_words else "Belirgin jargon yok"

    dossier_text = f"\n\nKİŞİLİK & DAVRANIŞ DOSYASI:\n{dossier}" if dossier else ""

    return f"""Sen Discord kullanıcısı '{display_name}' (@{user_name}) adlı kişinin birebir AI dijital klonusun.
GÖREVİN: {display_name} gibi düşünmek, onun gibi yazmak, onun fikirlerini savunmak ve sorulan soruya veya konuya onun üslubuyla cevap vermek.

{display_name} HAKKINDA BİLGİLER VE GERÇEK MESAJLARI:
- Sık kullandığı kelimeler/jargonlar: {words_text}
- Gerçek sunucu arşivinden mesaj örnekleri:
{samples_text}{dossier_text}

KURALLAR:
1. KESİNLİKLE bir yapay zeka, asistan veya bot olduğunu söyleme. Rolünden asla çıkma; sen doğrudan {display_name}'sin!
2. Onun imla ve konuşma tarzını birebir taklit et (küçük harf alışkanlığı, noktalama kullanmama veya bol küfür/argo kullanımı).
3. Uzun, resmi veya robotik paragraflar yazma! Tıpkı Discord'da arkadaşlarına yazar gibi kısa, doğal, vurucu ve duruma göre agresif/komik yaz.
4. Karşındaki soruya veya konuya {display_name} gözüyle doğrudan cevap ver."""


async def generate_clone_reply(
    target_user: discord.User | discord.Member,
    topic_or_question: str,
    config: dict[str, Any],
    curr_model: str,
    build_client_fn: Callable[[dict], Any],
) -> str:
    """Generates an impersonated response for target_user using the LLM."""
    data_dir = config.get("memory", {}).get("data_dir", "/var/lib/discord-ai-memory")
    sample_msgs, top_words, dossier = get_user_training_data(
        user_id=target_user.id,
        user_name=target_user.name,
        data_dir=data_dir,
    )

    system_prompt = build_impersonation_prompt(
        user_name=target_user.name,
        display_name=getattr(target_user, "display_name", target_user.name),
        sample_messages=sample_msgs,
        top_words=top_words,
        dossier=dossier,
    )

    api_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": topic_or_question.strip()},
    ]

    provider_name, model_name = curr_model.removesuffix(":vision").split("/", 1)
    provider_cfg = config.get("providers", {}).get(provider_name)
    if not provider_cfg:
        raise ValueError(f"Provider '{provider_name}' not configured")

    client = build_client_fn(provider_cfg)
    response = await completion(client,
        model=model_name,
        messages=api_messages,
        max_tokens=300,
        temperature=0.85,
    )

    reply = response.choices[0].message.content.strip()
    return reply


def build_clone_embed(
    target_user: discord.User | discord.Member,
    question_or_topic: str,
    clone_response: str,
) -> discord.Embed:
    """Builds a polished Discord Embed presenting the clone's response."""
    display_name = getattr(target_user, "display_name", target_user.name)
    embed = discord.Embed(
        description=f"### “{clone_response}”",
        color=0x9B59B6,
    )
    embed.set_author(
        name=f"{display_name} (AI Klonu)",
        icon_url=target_user.display_avatar.url,
    )
    embed.add_field(
        name="❓ Konu / Soru",
        value=f"*{question_or_topic}*",
        inline=False,
    )
    embed.set_footer(text="🎭 AI Kişilik Simülatörü | /taklit")
    return embed
