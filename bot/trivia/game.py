"""
Server Message Trivia Game ('Bunu Kim Söyledi?').
Selects iconic historical messages from the SQLite archive, generates 4 options,
and handles interactive Discord UI button gameplay with live leaderboards.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os
from pathlib import Path
import random
import sqlite3
from typing import Any, Optional

import discord

logger = logging.getLogger(__name__)

TURKISH_MONTHS = [
    "", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"
]


def _format_turkish_date(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        month_name = TURKISH_MONTHS[dt.month] if 1 <= dt.month <= 12 else str(dt.month)
        return f"{dt.day} {month_name} {dt.year}"
    except Exception:
        return iso_str[:10] if iso_str else "Bilinmiyor"


def _get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    # Ensure scores table exists
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trivia_scores (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT,
                points INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0,
                last_played TEXT
            )
        """)
    return conn


def get_random_trivia(
    data_dir: str = "/var/lib/discord-ai-memory",
    search_query: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """
    Pick a classic memorable message from active server members
    and select 3 distinct distractor options.
    """
    db_file = Path(data_dir) / "archive.sqlite3"
    if not db_file.exists():
        logger.warning("Trivia database not found at %s", db_file)
        return None

    conn = _get_db(str(db_file))
    try:
        cur = conn.cursor()

        # 1. Get top active human authors (excluding Deleted User and empty names)
        top_authors_rows = cur.execute("""
            SELECT author, name, count(*) as cnt 
            FROM messages 
            WHERE name != 'Deleted User' AND length(name) > 1
            GROUP BY author 
            HAVING cnt >= 50
            ORDER BY cnt DESC
            LIMIT 20
        """).fetchall()

        if not top_authors_rows or len(top_authors_rows) < 4:
            return None

        author_map = {r["author"]: r["name"] for r in top_authors_rows}
        author_ids = list(author_map.keys())

        # 2. Pick candidate quote messages
        placeholders = ",".join("?" for _ in author_ids)
        params: list[Any] = list(author_ids)

        query = f"""
            SELECT id, author, name, created, content, url
            FROM messages
            WHERE author IN ({placeholders})
              AND length(content) BETWEEN 25 AND 180
              AND content NOT LIKE 'http%'
              AND content NOT LIKE '!%'
              AND content NOT LIKE '/%'
              AND content NOT LIKE '<@%'
        """

        if search_query and search_query.strip():
            query += " AND content LIKE ?"
            params.append(f"%{search_query.strip()}%")

        query += " ORDER BY RANDOM() LIMIT 20"

        candidates = cur.execute(query, params).fetchall()
        if not candidates:
            return None

        # Filter out candidate messages where the author's own name appears in the text
        selected = None
        for row in candidates:
            c_text = row["content"].lower()
            a_name = row["name"].lower()
            if a_name not in c_text and not any(part in c_text for part in a_name.split() if len(part) > 2):
                selected = row
                break

        if not selected:
            selected = candidates[0]

        correct_author_id = selected["author"]
        correct_author_name = selected["name"]

        # 3. Pick 3 distinct distractor names (case-insensitively unique from correct author)
        seen_names = {correct_author_name.strip().lower()}
        distractors: list[str] = []

        # Alias grouping: 'sez' and 'sezrage' are the same person
        if "sez" in correct_author_name.lower():
            seen_names.add("sez")
            seen_names.add("sezrage")

        shuffled_authors = list(top_authors_rows)
        random.shuffle(shuffled_authors)

        for a in shuffled_authors:
            cand_name = a["name"].strip()
            cand_lower = cand_name.lower()
            if cand_lower not in seen_names:
                distractors.append(cand_name)
                seen_names.add(cand_lower)
                if len(distractors) >= 3:
                    break

        if len(distractors) < 3:
            distractors += ["Onur", "Sezin", "Apro"][: 3 - len(distractors)]

        # 4. Create shuffled 4 options
        options = [correct_author_name] + distractors[:3]
        random.shuffle(options)

        return {
            "message_id": selected["id"],
            "content": selected["content"],
            "correct_author_id": correct_author_id,
            "correct_author_name": correct_author_name,
            "created": selected["created"],
            "date_str": _format_turkish_date(selected["created"]),
            "url": selected["url"] or f"https://discord.com/channels/{selected['id']}",
            "options": options,
        }
    finally:
        conn.close()


def record_trivia_win(
    user_id: int,
    user_name: str,
    data_dir: str = "/var/lib/discord-ai-memory",
) -> int:
    """Awards +10 points to the winner and returns new total points."""
    db_file = Path(data_dir) / "archive.sqlite3"
    conn = _get_db(str(db_file))
    try:
        now_iso = datetime.now().isoformat()
        with conn:
            conn.execute("""
                INSERT INTO trivia_scores (user_id, user_name, points, wins, attempts, last_played)
                VALUES (?, ?, 10, 1, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    user_name = excluded.user_name,
                    points = trivia_scores.points + 10,
                    wins = trivia_scores.wins + 1,
                    attempts = trivia_scores.attempts + 1,
                    last_played = excluded.last_played
            """, (user_id, user_name, now_iso))

            row = conn.execute("SELECT points FROM trivia_scores WHERE user_id = ?", (user_id,)).fetchone()
            return row["points"] if row else 10
    finally:
        conn.close()


def get_trivia_leaderboard(
    data_dir: str = "/var/lib/discord-ai-memory",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Returns top trivia scorers."""
    db_file = Path(data_dir) / "archive.sqlite3"
    if not db_file.exists():
        return []

    conn = _get_db(str(db_file))
    try:
        rows = conn.execute("""
            SELECT user_id, user_name, points, wins, attempts
            FROM trivia_scores
            ORDER BY points DESC, wins DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class TriviaButton(discord.ui.Button):
    def __init__(self, label: str, option_idx: int):
        super().__init__(
            label=f"{['A', 'B', 'C', 'D'][option_idx]}) {label}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"trivia_opt_{option_idx}",
        )
        self.option_name = label

    async def callback(self, interaction: discord.Interaction):
        view: TriviaView = self.view  # type: ignore
        await view.handle_guess(interaction, self)


class TriviaView(discord.ui.View):
    def __init__(
        self,
        trivia_data: dict[str, Any],
        data_dir: str = "/var/lib/discord-ai-memory",
        timeout: float = 45.0,
    ):
        super().__init__(timeout=timeout)
        self.trivia = trivia_data
        self.data_dir = data_dir
        self.correct_name = trivia_data["correct_author_name"]
        self.answered = False
        self.message: Optional[discord.Message] = None

        # Add 4 choice buttons
        for idx, option_name in enumerate(trivia_data["options"]):
            self.add_item(TriviaButton(label=option_name, option_idx=idx))

    async def handle_guess(self, interaction: discord.Interaction, button: TriviaButton):
        if self.answered:
            await interaction.response.send_message("Bu soru zaten cevaplandı!", ephemeral=True)
            return

        chosen_name = button.option_name

        if chosen_name == self.correct_name:
            # Correct answer!
            self.answered = True
            button.style = discord.ButtonStyle.success

            # Disable all buttons
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True

            new_points = record_trivia_win(
                interaction.user.id,
                interaction.user.display_name,
                self.data_dir,
            )

            embed = self.build_embed(
                status=f"🎉 **TEBRİKLER!** {interaction.user.mention} doğru bildi! (+10 Puan, Toplam: **{new_points}**)",
                revealed=True,
            )

            await interaction.response.edit_message(embed=embed, view=self)
            self.stop()
        else:
            # Wrong answer!
            button.style = discord.ButtonStyle.danger
            button.disabled = True

            # Check if all 3 wrong buttons clicked
            remaining = [
                child for child in self.children
                if isinstance(child, discord.ui.Button) and not child.disabled
            ]

            embed = self.build_embed(
                status=f"❌ {interaction.user.display_name} **{chosen_name}** dedi ama yanlış çıktı!",
                revealed=False,
            )

            await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        if not self.answered:
            self.answered = True
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
                    if child.option_name == self.correct_name:
                        child.style = discord.ButtonStyle.success

            embed = self.build_embed(
                status=f"⏰ **SÜRE DOLDU!** Kimse bilemedi. Bu sözün asıl sahibi: **{self.correct_name}**",
                revealed=True,
            )

            if self.message:
                try:
                    await self.message.edit(embed=embed, view=self)
                except Exception as e:
                    logger.debug("Could not edit timed out trivia message: %s", e)

    def build_embed(self, status: Optional[str] = None, revealed: bool = False) -> discord.Embed:
        embed = discord.Embed(
            title="❓ BUNU KİM SÖYLEDİ? (Sunucu Trivia)",
            description=f"### “{self.trivia['content']}”\n\n📅 **Tarih:** {self.trivia['date_str']}",
            color=0x5865F2 if not revealed else (0x57F287 if self.answered else 0xED4245),
        )

        if revealed:
            embed.add_field(
                name="Sözün Sahibi",
                value=f"👑 **{self.correct_name}**\n🔗 [Orijinal Mesaja Git]({self.trivia['url']})",
                inline=False,
            )

        if status:
            embed.set_footer(text=status.replace("**", "").replace("###", "").replace("🎉", "✨"))
        else:
            embed.set_footer(text="Aşağıdaki butonlardan doğru kişiyi seç! Süre: 45 saniye")

        return embed
