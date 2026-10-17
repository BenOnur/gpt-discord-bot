"""Natural-language Discord member join date and compact activity history."""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import discord

from bot.llm.tools._types import ToolEntry
from bot.llm.tools.discord_ctx import get_current_discord_context
from bot.storage import ClosingConnection

logger = logging.getLogger(__name__)


def _clean_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold()).replace("ı", "i")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"<@!?(\d+)>", r"\1", value)
    value = " ".join(re.sub(r"[^\w]+", " ", value).split())
    # Turkish genitive forms commonly appear in natural-language requests:
    # "Onur'un" / "Onurun" should still resolve the member named Onur.
    value = re.sub(r"(?:\s+(?:un|in|nun|nin|nın|nün|ın|ün)|(?:un|in|nun|nin|nın|nün))$", "", value)
    return value


class PresenceStore:
    """Bounded SQLite history: activity name/type and start/end times only."""

    def __init__(self, path: str, retention_days: int = 30, max_records: int = 1000):
        self.path = Path(path)
        self.retention_days = max(1, int(retention_days))
        self.max_records = max(100, int(max_records))
        self._updates = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        import sqlite3

        connection = sqlite3.connect(self.path, timeout=5, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        import sqlite3

        with sqlite3.connect(self.path, timeout=5, factory=ClosingConnection) as db:
            db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS activity_sessions (
                    id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    activity_type INTEGER NOT NULL,
                    activity_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS activity_user_recent
                ON activity_sessions(guild_id, user_id, started_at DESC)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            db.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES('tracking_started_at', ?)",
                (datetime.now(timezone.utc).isoformat(),),
            )

    @staticmethod
    def _activities(activities: Iterable[Any]) -> dict[tuple[int, str], tuple[int, str]]:
        result: dict[tuple[int, str], tuple[int, str]] = {}
        for activity in activities or ():
            name = str(getattr(activity, "name", "") or "").strip()
            if isinstance(activity, discord.Spotify):
                name = f"{activity.title} — {', '.join(activity.artists)}"
            if not name or name.casefold() in {"custom status", "custom"}:
                continue
            try:
                activity_type = int(getattr(activity, "type", 0))
            except (TypeError, ValueError):
                activity_type = 0
            if activity_type == int(discord.ActivityType.custom):
                continue
            # Activity names are the only rich-presence text retained; details,
            # party IDs, URLs, status text and other payload fields are discarded.
            name = name[:100]
            result[(activity_type, name.casefold())] = (activity_type, name)
        return result

    def update(self, guild_id: int, user_id: int, activities: Iterable[Any], observed_at: datetime | None = None) -> None:
        now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        current = self._activities(activities)
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, activity_type, activity_name FROM activity_sessions "
                "WHERE guild_id=? AND user_id=? AND ended_at IS NULL",
                (guild_id, user_id),
            ).fetchall()
            existing = {(row["activity_type"], row["activity_name"].casefold()): row["id"] for row in rows}
            for key, row_id in existing.items():
                if key not in current:
                    db.execute("UPDATE activity_sessions SET ended_at=? WHERE id=?", (now, row_id))
            for key, (activity_type, name) in current.items():
                if key not in existing:
                    db.execute(
                        "INSERT INTO activity_sessions "
                        "(guild_id,user_id,activity_type,activity_name,started_at) VALUES(?,?,?,?,?)",
                        (guild_id, user_id, activity_type, name, now),
                    )

        self._updates += 1
        if self._updates % 100 == 0:
            self.prune(now)

    def tracking_started_at(self) -> str:
        with self._connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='tracking_started_at'").fetchone()
        return row[0] if row else "unknown"

    def history(self, guild_id: int, user_id: int, limit: int = 8) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT activity_type,activity_name,started_at,ended_at FROM activity_sessions "
                "WHERE guild_id=? AND user_id=? ORDER BY started_at DESC,id DESC LIMIT ?",
                (guild_id, user_id, min(max(int(limit), 1), 20)),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune(self, now_iso: str | None = None) -> None:
        now = datetime.fromisoformat(now_iso) if now_iso else datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=self.retention_days)).isoformat()
        with self._connect() as db:
            db.execute("DELETE FROM activity_sessions WHERE started_at < ?", (cutoff,))
            db.execute("""
                DELETE FROM activity_sessions WHERE id IN (
                    SELECT id FROM activity_sessions ORDER BY started_at DESC,id DESC LIMIT -1 OFFSET ?
                )
            """, (self.max_records,))
            db.execute("PRAGMA incremental_vacuum")


class PresenceTracker:
    def __init__(self, config: dict[str, Any]):
        settings = config.get("presence") or {}
        self.enabled = bool(settings.get("enabled", False))
        self.guild_ids = {int(value) for value in settings.get("guild_ids", []) if str(value).isdigit()}
        path = settings.get("data_path", "/var/lib/discord-ai-memory/rich_presence.sqlite3")
        self.store = PresenceStore(
            path,
            retention_days=settings.get("retention_days", 30),
            max_records=settings.get("max_records", 1000),
        ) if self.enabled else None
        if self.store:
            self.store.prune()

    def tracks(self, guild_id: int) -> bool:
        return self.enabled and (not self.guild_ids or guild_id in self.guild_ids)

    def observe(self, member: discord.Member) -> None:
        if self.store is None or member.guild is None or not self.tracks(member.guild.id):
            return
        try:
            self.store.update(member.guild.id, member.id, member.activities)
        except Exception:
            logger.exception("Could not store Discord activity update for member %s", member.id)


def _resolve_member(target: str | None, ctx: dict[str, Any]):
    guild = ctx.get("guild")
    message = ctx.get("message")
    if guild is None:
        return None, "Üye bilgilerini yalnızca Discord sunucusu içindeki bir kanalda sorgulayabilirim."
    bot_id = getattr(getattr(ctx.get("bot"), "user", None), "id", None)
    mentions = [member for member in getattr(message, "mentions", ()) if member.id != bot_id]
    query = _clean_name(target or "")
    if len(mentions) == 1 and (not query or query in {str(mentions[0].id), _clean_name(mentions[0].name), _clean_name(mentions[0].display_name)}):
        return mentions[0], None
    if len(mentions) > 1:
        matches = [m for m in mentions if query and query in {str(m.id), _clean_name(m.name), _clean_name(m.display_name)}]
        if len(matches) == 1:
            return matches[0], None
        return None, "Birden fazla kişi etiketlenmiş; sorgulamak istediğin kişiyi belirt veya tek başına etiketle."
    if not query:
        content = _clean_name(getattr(message, "content", ""))
        candidates = {}
        for member in getattr(guild, "members", ()):
            names = (member.name, member.display_name, getattr(member, "global_name", None) or "")
            if any(name and re.search(rf"(?<!\w){re.escape(_clean_name(name))}(?!\w)", content) for name in names):
                candidates[member.id] = member
        if len(candidates) == 1:
            return next(iter(candidates.values())), None
        if len(candidates) > 1:
            return None, "Birden fazla üyeden söz ediliyor; sorgulamak istediğin kişiyi etiketle."
        return ctx.get("author"), None
    if query.isdigit():
        member = guild.get_member(int(query))
        if member:
            return member, None
    candidates = {}
    for member in getattr(guild, "members", ()):
        names = (member.name, member.display_name, getattr(member, "global_name", None) or "")
        if query in {_clean_name(name) for name in names if name}:
            candidates[member.id] = member
    if len(candidates) == 1:
        return next(iter(candidates.values())), None
    if len(candidates) > 1:
        return None, f"“{target}” birden fazla üyeyle eşleşiyor; kişiyi etiketle."
    return None, f"“{target}” adlı üyeyi bu sunucuda bulamadım. Kişiyi etiketleyebilir veya tam kullanıcı adını yazabilirsin."


def member_info_request(text: str) -> str | None:
    """Classify clear join-date and activity questions for a deterministic fast path."""
    text = _clean_name(text)
    if any(term in text for term in ("dinledigim", "dinliyorum", "dinledigi", "spotify", "hangi sarki", "ne dinliyor")):
        return "music_research" if any(term in text for term in ("arastir", "bilgi", "hakkinda")) else "current_music"
    if any(term in text for term in ("ne zaman sunucuya girdi", "ne zaman sunucuya katildi", "sunucuya katilma tarihi", "ne zamandir sunucuda", "katildigi tarih")):
        return "joined_at"
    if any(term in text for term in ("oyun gecmisi", "etkinlik gecmisi", "rich presence", "presence gecmisi", "hangi oyunlari oynamis", "oynadigi oyunlar")):
        return "activity_history"
    if any(term in text for term in ("su an ne oynuyor", "su anda ne oynuyor", "hangi oyunu oynuyor", "simdi ne oynuyor", "ne oynuyor su an")):
        return "current_activity"
    if any(term in text for term in ("en son hangi oyunu", "son oynadigi oyun", "en son oynadigi", "son hangi oyunu oynadi", "en son ne oynadi", "en son hangi oyunda")):
        return "last_game"
    return None


async def _async_get_member_info(target: str | None, include: str, ctx: dict[str, Any]) -> str:
    guild = ctx.get("guild")
    member, error = _resolve_member(target, ctx)
    if error:
        return error
    if member is None:
        return "Sorgulanacak sunucu üyesini bulamadım."
    if guild.get_member(member.id) is None:
        try:
            member = await guild.fetch_member(member.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    joined = getattr(member, "joined_at", None)
    if joined is not None:
        joined = joined.astimezone().strftime("%d.%m.%Y %H:%M (Türkiye saati)")
    else:
        joined = "Discord bu üyenin sunucuya katılma tarihini vermedi."

    activities = PresenceStore._activities(getattr(member, "activities", ()))
    if include in ("current_music", "music_research"):
        for activity in getattr(member, "activities", ()):
            if isinstance(activity, discord.Spotify):
                return (f"{member.display_name} şu an Spotify'da {activity.title} — {', '.join(activity.artists)} dinliyor. "
                        f"Albüm: {activity.album}. Kaynak: {activity.track_url}")
        tracker = ctx.get("presence_tracker")
        if not tracker or not tracker.enabled:
            return "Etkinlik takibi şu an kapalı; Discord'da dinlediğin şarkıya erişemiyorum. İzin açılınca paylaşılan Spotify etkinliğini okuyabilirim."
        return f"{member.display_name} için Discord'da paylaşılan güncel Spotify parçası görünmüyor. Etkinlik paylaşımını kontrol edebilir veya şarkının adını/linkini gönderebilirsin."
    current = list(activities.values())
    tracker: PresenceTracker | None = ctx.get("presence_tracker")
    records = []
    started = None
    if tracker and tracker.store and tracker.tracks(guild.id):
        records = tracker.store.history(guild.id, member.id, 8)
        started = tracker.store.tracking_started_at()

    def label(activity_type: int, name: str) -> str:
        if activity_type == int(discord.ActivityType.playing):
            return f"{name} oynuyor"
        if activity_type == int(discord.ActivityType.listening):
            return f"{name} dinliyor"
        if activity_type == int(discord.ActivityType.streaming):
            return f"{name} yayını"
        if activity_type == int(discord.ActivityType.watching):
            return f"{name} izliyor"
        return name

    if include == "joined_at":
        return f"{member.display_name} sunucuya {joined} katılmış."
    if include == "current_activity":
        return f"{member.display_name} şu an: " + (", ".join(label(*item) for item in current) if current else "Discord’da görünür bir etkinlik paylaşmıyor.")
    if include == "last_game":
        current_games = [(t, n) for t, n in current if t == int(discord.ActivityType.playing)]
        if current_games:
            return f"{member.display_name} şu anda " + ", ".join(n for _, n in current_games) + " oynuyor."
        last_game = next((row for row in records if int(row["activity_type"]) == int(discord.ActivityType.playing)), None)
        if last_game:
            ended = datetime.fromisoformat(last_game["ended_at"]).astimezone().strftime("%d.%m.%Y %H:%M") if last_game["ended_at"] else "son görüldüğünde hâlâ açıktı"
            return f"Kaydedilmiş son oyunu {last_game['activity_name']} ({ended})."
        return f"{member.display_name} için henüz kaydedilmiş bir oyun yok. İzleme başlangıcı: {started or 'bilinmiyor'}. Önceki oyun geçmişi Discord’dan geriye dönük alınamıyor."
    if include == "activity_history":
        if not records:
            return f"{member.display_name} için henüz etkinlik geçmişi kaydedilmemiş. İzleme başlangıcı: {started or 'bilinmiyor'}."
        lines = []
        for row in records:
            when = datetime.fromisoformat(row["started_at"]).astimezone().strftime("%d.%m.%Y %H:%M")
            lines.append(f"{when}: {label(int(row['activity_type']), row['activity_name'])}")
        return f"{member.display_name} için botun kaydettiği son Discord etkinlikleri:\n" + "\n".join(lines)

    activity_text = ", ".join(label(*item) for item in current) if current else "paylaşılan güncel etkinlik yok"
    return f"{member.display_name}: sunucuya katılma tarihi {joined}; şu an {activity_text}."


def get_member_info(target: str = "", include: str = "summary") -> str:
    """Resolve a member and return join date or observed activity data."""
    ctx = get_current_discord_context()
    bot = ctx.get("bot")
    if not bot or not getattr(getattr(bot, "loop", None), "is_running", lambda: False)():
        return "Üye bilgilerini almak için etkin bir Discord mesajı gerekli."
    future = asyncio.run_coroutine_threadsafe(_async_get_member_info(target, include, ctx), bot.loop)
    try:
        return future.result(timeout=20)
    except Exception as exc:
        logger.exception("get_member_info failed")
        return f"Üye bilgisi alınırken hata oluştu: {type(exc).__name__}."


MEMBER_INFO_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_member_info",
        "description": "Read a Discord member's visible live Spotify song (current_music), join date, current activity, last game or observed activity history. For what I am listening to, use current_music with empty target (requesting user), then web_search the returned song/artist if research is requested. Never invent activity. Resolve a tagged member or exact name.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Member username, server display name, user ID, or omit when one person is tagged or the question is about the message author."},
                "include": {"type": "string", "enum": ["summary", "joined_at", "current_activity", "current_music", "last_game", "activity_history"], "description": "Select join date, current activity, live Spotify song, last game or observed history."},
            },
        },
    },
}

TOOL_NAME = "get_member_info"
TOOL_ENTRY = ToolEntry(schema=MEMBER_INFO_SCHEMA, fn=get_member_info)
