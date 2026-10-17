"""
Dynamic reminder tool with SQLite persistence and APScheduler integration.
Allows users to set natural language reminders to be pinged (tagged) later.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import logging
import os
import sqlite3
import zoneinfo
import discord
from pathlib import Path
from typing import Any

from bot.llm.tools._types import ToolEntry
from bot.llm.tools.discord_ctx import get_current_discord_context

_DB_PATH = Path(os.getenv("DISCORD_MEMORY_DIR", "/var/lib/discord-ai-memory")) / "reminders.sqlite3"
_SCHEDULER = None


@contextmanager
def _get_db():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(_DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    with db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                channel_id INTEGER,
                user_id INTEGER,
                trigger_at REAL,
                reason TEXT,
                completed INTEGER DEFAULT 0,
                created_at TEXT
            );
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_reminders_pending ON reminders(completed, trigger_at);")
        columns = {row[1] for row in db.execute("PRAGMA table_info(reminders)")}
        for name, declaration in (("status", "TEXT DEFAULT 'pending'"), ("attempts", "INTEGER DEFAULT 0"), ("dedupe", "TEXT")):
            if name not in columns:
                db.execute(f"ALTER TABLE reminders ADD COLUMN {name} {declaration}")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS reminders_dedupe ON reminders(dedupe)")
        db.execute("UPDATE reminders SET status='sent' WHERE completed=1 AND status='pending'")
    try:
        with db:
            yield db
    finally:
        db.close()


async def _send_reminder(
    bot: Any, channel_id: int, user_id: int, reason: str, reminder_id: int
) -> None:
    """Async callback executed when reminder is due."""
    try:
        with _get_db() as db:
            row = db.execute("SELECT completed,status FROM reminders WHERE id=?", (reminder_id,)).fetchone()
            if not row or row[0] or row[1] in ("cancelled", "sent"):
                return
            db.execute("UPDATE reminders SET attempts=attempts+1,status='sending' WHERE id=?", (reminder_id,))
        channel = bot.get_channel(channel_id)
        if not channel:
            channel = await bot.fetch_channel(channel_id)
        if channel:
            await asyncio.wait_for(channel.send(f"<@{user_id}> ⏰ **Hatırlatıcı:** {reason}",
                                   allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=user_id)], roles=False, everyone=False)), 15)
            logging.info("Sent reminder id=%s to user=%s in channel=%s", reminder_id, user_id, channel_id)
        else:
            raise RuntimeError("Reminder channel unavailable")
        with _get_db() as db:
            db.execute("UPDATE reminders SET completed=1,status='sent' WHERE id=?", (reminder_id,))
    except Exception as e:
        logging.error("Failed to send reminder %s (%s)", reminder_id, type(e).__name__)
        with _get_db() as db:
            attempts = db.execute("SELECT attempts FROM reminders WHERE id=?", (reminder_id,)).fetchone()[0]
            db.execute("UPDATE reminders SET status=? WHERE id=?", ("failed" if attempts >= 3 else "pending", reminder_id))
        if attempts < 3 and _SCHEDULER:
            _SCHEDULER.add_job(_send_reminder, "date", run_date=datetime.now(timezone.utc) + timedelta(seconds=60 * attempts),
                               id=f"reminder_{reminder_id}", args=[bot, channel_id, user_id, reason, reminder_id], replace_existing=True, misfire_grace_time=120)


def restore_pending_reminders(bot: Any, scheduler: Any) -> None:
    """Load and schedule all uncompleted reminders from SQLite on bot startup."""
    global _SCHEDULER
    _SCHEDULER = scheduler
    try:
        now_ts = datetime.now(timezone.utc).timestamp()
        with _get_db() as db:
            rows = db.execute("SELECT * FROM reminders WHERE completed = 0 AND status NOT IN ('cancelled','failed') ORDER BY trigger_at").fetchall()

        restored_count = 0
        for r in rows:
            run_date = datetime.fromtimestamp(r["trigger_at"], tz=timezone.utc)
            # If past due, fire immediately (within 2 seconds)
            if r["trigger_at"] <= now_ts:
                run_date = datetime.now(timezone.utc) + timedelta(seconds=2)

            job_id = f"reminder_{r['id']}"
            try:
                scheduler.add_job(
                    _send_reminder,
                    "date",
                    run_date=run_date,
                    id=job_id,
                    args=[bot, r["channel_id"], r["user_id"], r["reason"], r["id"]],
                    replace_existing=True,
                    misfire_grace_time=120,
                )
                restored_count += 1
            except Exception as e:
                logging.warning("Failed to restore reminder %s: %s", r["id"], e)

        if restored_count > 0:
            logging.info("Restored %d pending reminder(s) from database", restored_count)
    except Exception as e:
        logging.error("Error restoring pending reminders: %s", e)


def set_reminder(duration_minutes: float, reminder_text: str) -> str:
    """Set a reminder to tag the user after duration_minutes."""
    if not 0 < duration_minutes <= 525600:
        return "Hatırlatıcı süresi 0'dan büyük olmalıdır."

    clean_reason = reminder_text.strip()[:500]
    if not clean_reason:
        return "Hatırlatıcı için bir konu veya açıklama belirtilmedi."

    ctx = get_current_discord_context()
    channel = ctx.get("channel")
    author = ctx.get("author")
    guild = ctx.get("guild")
    bot = ctx.get("bot")
    scheduler = ctx.get("scheduler")
    global _SCHEDULER
    _SCHEDULER = scheduler or _SCHEDULER

    if not channel or not author:
        return f"Discord bağlamı bulunamadı. Hatırlatıcı kurulamadı."

    now_utc = datetime.now(timezone.utc)
    trigger_dt = now_utc + timedelta(minutes=duration_minutes)
    trigger_ts = trigger_dt.timestamp()

    # Persist in SQLite
    with _get_db() as db:
        message = ctx.get("message")
        dedupe = str(message.id) if message and isinstance(message.id, int) else None
        existing = db.execute("SELECT id FROM reminders WHERE dedupe=?", (dedupe,)).fetchone() if dedupe else None
        if existing:
            return f"Bu mesaj için hatırlatıcı zaten kayıtlı: #{existing[0]}."
        cur = db.execute(
            """INSERT INTO reminders (guild_id, channel_id, user_id, trigger_at, reason, completed, created_at,dedupe)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
            (
                guild.id if guild else None,
                channel.id,
                author.id,
                trigger_ts,
                clean_reason,
                now_utc.isoformat(),
                dedupe,
            ),
        )
        reminder_id = cur.lastrowid

    # Schedule via APScheduler
    if scheduler and bot:
        job_id = f"reminder_{reminder_id}"
        # Schedule threadsafe on the bot's event loop
        def _schedule():
            scheduler.add_job(
                _send_reminder,
                "date",
                run_date=trigger_dt,
                id=job_id,
                args=[bot, channel.id, author.id, clean_reason, reminder_id],
                replace_existing=True,
            )

        if bot.loop and bot.loop.is_running():
            bot.loop.call_soon_threadsafe(_schedule)
        else:
            _schedule()

    # Format human-friendly time in Turkey timezone
    tz_tr = zoneinfo.ZoneInfo("Europe/Istanbul")
    target_tr = trigger_dt.astimezone(tz_tr).strftime("%H:%M:%S")

    mins_text = f"{duration_minutes:.1f}".rstrip("0").rstrip(".")
    return (
        f"Hatırlatıcı kuruldu! {mins_text} dakika sonra (saat {target_tr}) "
        f"seni bu kanalda etiketleyip '{clean_reason}' konusunu hatırlatacağım."
    )


REMINDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_reminder",
        "description": "Set a reminder to ping (tag) the user in the Discord channel after a specified number of minutes. Use when a user asks to be reminded about something (e.g., '1 saat sonra beni etiketle', '20 dakika sonra hatırlat').",
        "parameters": {
            "type": "object",
            "properties": {
                "duration_minutes": {
                    "type": "number",
                    "description": "Minutes from now until reminder fires (e.g. 5, 20, 60, 1.5)",
                },
                "reminder_text": {
                    "type": "string",
                    "description": "What to remind the user about (e.g. 'fırını kapat', 'maç başladı', 'ilacını iç')",
                },
            },
            "required": ["duration_minutes", "reminder_text"],
        },
    },
}

TOOL_NAME = "set_reminder"
TOOL_ENTRY = ToolEntry(
    schema=REMINDER_SCHEMA,
    fn=set_reminder,
)
