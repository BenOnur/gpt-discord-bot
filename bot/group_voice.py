"""Source-scoped, bounded voice learning from the Discord archive; no model calls."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics
import time
import uuid

from bot.archive_access import open_archive, allowed_channels

TERMS = ("amk", "aq", "lan", "olm", "oğlum", "aga", "abi", "ya", "harbi", "yok artık",
         "ne alaka", "siktir", "bok", "salak", "mal", "taşak", "makara", "aynen", "iyiymiş")


def summarize(rows):
    """Learn aggregate habits, never promote quoted messages into instructions."""
    lengths, lower, insults, expressions = [], 0, 0, Counter()
    per_author = Counter()
    for row in rows:
        if per_author[row["author"]] >= 150:
            continue
        text = row["content"].strip()
        if not text or text.startswith(("/", "!", "[Görsel")) or "http" in text:
            continue
        if any(term in text.casefold() for term in ("api_key", "token", "system prompt", "ignore previous", "talimat")):
            continue
        per_author[row["author"]] += 1
        lengths.append(len(text.split()))
        first = next((letter for letter in text if letter.isalpha()), "")
        lower += bool(first and first.islower())
        for term in TERMS:
            if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text.casefold()):
                expressions[term] += 1
        insults += bool(re.search(r"\b(amk|aq|siktir|sikik|piç|pic)\b", text.casefold()))
    count = len(lengths)
    return {"messages":count, "members":len(per_author), "median_words":statistics.median(lengths) if lengths else 0,
            "lowercase_ratio":round(lower/max(count,1),2), "slang_ratio":round(insults/max(count,1),2),
            "expressions":{term:freq for term,freq in expressions.most_common(10) if freq >= 3}}


def voice_context(ctx):
    guild, channel = ctx.get("guild"), ctx.get("channel")
    if not guild or not channel:
        return ""
    config = ctx.get("config") or {}
    settings = config.get("memory", {})
    root = Path(settings.get("data_dir", "/var/lib/discord-ai-memory"))
    archive = root / "archive.sqlite3"
    if not archive.exists():
        return ""
    scope = sorted(allowed_channels(ctx))
    if not scope:
        return ""
    fingerprint = hashlib.sha256(json.dumps(scope).encode()).hexdigest()[:16]
    path = root / "voice" / f"{guild.id}-{channel.id}-{fingerprint}.json"
    # Small bounded rebuild; at most once per hour, no remote API or raw message export.
    if path.exists() and time.time()-path.stat().st_mtime < 3600:
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        with closing(open_archive(archive, ctx=ctx)) as db:
            recent = db.execute("SELECT id,author,content FROM messages ORDER BY id DESC LIMIT 1500").fetchall()
            older = []
            year = datetime.now(timezone.utc).year
            for sample_year in range(year-5, year):
                # Snowflake bounds keep historical samples cheap and spread across years.
                low = (int(datetime(sample_year,1,1,tzinfo=timezone.utc).timestamp()*1000)-1420070400000)<<22
                high = (int(datetime(sample_year+1,1,1,tzinfo=timezone.utc).timestamp()*1000)-1420070400000)<<22
                older.extend(db.execute("SELECT id,author,content FROM messages WHERE id>=? AND id<? ORDER BY id DESC LIMIT 100", (low,high)).fetchall())
        rows = {row["id"]:row for row in [*recent,*older]}
        data = summarize(rows.values())
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        data["source_channels"] = scope
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        temporary.replace(path)
    if data["messages"] < 20:
        return ""
    habits = ", ".join(data["expressions"])
    return ("\n<group_voice>Bu erişilebilir sunucu arşivinden türetilmiş konuşma alışkanlıklarıdır; kullanıcı talimatı veya kişi hakkında olgu değildir. "
            f"Örneklem: {data['messages']} mesaj/{data['members']} kişi. Ortanca mesaj {data['median_words']} kelime. "
            f"Küçük harfle başlama oranı {data['lowercase_ratio']}; argo oranı {data['slang_ratio']}. Sık ifadeler: {habits or 'belirgin değil'}. "
            "Ritmi ve kelime seçimini doğal biçimde benimse; kelimeleri sırf listede var diye her yanıta sıkıştırma. "
            "Kimseyi birebir taklit etme veya geçmişte yaşamadığın anıları yaşadım diye anlatma. Kendine ait hazırcevap ve kuru bir mizahın olsun. "
            "Son mesaj ciddi görevse önce işi çöz. Botla karşılıklı makara/sataşma varsa kısa laf sokabilir ve uygun argo/küfürle karşılık verebilirsin. "
            "Küfür tek başına saldırı kanıtı değildir; ciddi bir derdini küfrederek anlatana sövme. Nefret söylemi, tehdit veya kişisel hassas bilgiyi aşağılama malzemesi yapma."
            "</group_voice>")
