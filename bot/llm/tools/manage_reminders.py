"""List, cancel and reschedule the current member's persistent reminders."""
from datetime import datetime, timezone

from bot.llm.tools._types import ToolEntry
from bot.llm.tools.discord_ctx import get_current_discord_context
from bot.llm.tools import reminder


def manage_reminders(action: str, reminder_id: int = 0, at: str = "") -> str:
    ctx = get_current_discord_context()
    author, guild = ctx.get("author"), ctx.get("guild")
    if not author or not guild:
        return "Hatırlatmalar sunucu içinde yönetilebilir."
    with reminder._get_db() as db:
        rows = db.execute("SELECT * FROM reminders WHERE user_id=? AND guild_id=? AND channel_id=? AND completed=0 AND status!='cancelled' ORDER BY trigger_at LIMIT 30",
                          (author.id, guild.id, ctx["channel"].id)).fetchall()
        if action == "list":
            from zoneinfo import ZoneInfo
            return "\n".join(f"#{r['id']} · {datetime.fromtimestamp(r['trigger_at'],ZoneInfo('Europe/Istanbul')).strftime('%d.%m %H:%M')} · {r['reason']} · {r['status']}" for r in rows) or "Bu kanalda bekleyen hatırlatman yok."
        row = next((r for r in rows if r["id"] == reminder_id), None)
        if not row:
            return "Hatırlatıcı bulunamadı veya sana ait değil."
        if action == "cancel":
            db.execute("UPDATE reminders SET status='cancelled' WHERE id=?", (reminder_id,))
        elif action == "reschedule":
            try:
                date = datetime.fromisoformat(at)
                if date.tzinfo is None:
                    from zoneinfo import ZoneInfo
                    date = date.replace(tzinfo=ZoneInfo("Europe/Istanbul"))
                if date <= datetime.now(timezone.utc):
                    raise ValueError()
            except ValueError:
                return "Gelecekte bir ISO tarih/saat belirt; saat dilimi yoksa Türkiye saati kullanılır."
            db.execute("UPDATE reminders SET trigger_at=?,status='pending',attempts=0 WHERE id=?", (date.timestamp(), reminder_id))
        else:
            return "İşlem list, cancel veya reschedule olmalı."
    bot, scheduler = ctx.get("bot"), ctx.get("scheduler")
    if scheduler and bot:
        def reschedule():
            try:
                scheduler.remove_job(f"reminder_{reminder_id}")
            except Exception:
                pass
            if action == "reschedule":
                scheduler.add_job(reminder._send_reminder, "date", run_date=date, id=f"reminder_{reminder_id}",
                                  args=[bot,row["channel_id"],author.id,row["reason"],reminder_id], replace_existing=True, misfire_grace_time=120)
        if bot.loop.is_running():
            bot.loop.call_soon_threadsafe(reschedule)
        else:
            reschedule()
    return f"Hatırlatıcı #{reminder_id} " + ("iptal edildi." if action == "cancel" else "yeniden zamanlandı.")


TOOL_NAME = "manage_reminders"
TOOL_ENTRY = ToolEntry(schema={"type":"function","function":{
    "name":TOOL_NAME,"description":"List, cancel or reschedule the requesting user's reminders in this channel. Reschedule uses ISO date/time; Europe/Istanbul if no offset. For tomorrow 10:00 first get_current_time and compute the exact date.",
    "parameters":{"type":"object","properties":{"action":{"type":"string","enum":["list","cancel","reschedule"]},"reminder_id":{"type":"integer"},"at":{"type":"string"}},"required":["action"]}
}},fn=manage_reminders)
