"""
Current date, time, and timezone information tool.
"""
from __future__ import annotations

from datetime import datetime
import zoneinfo

from bot.llm.tools._types import ToolEntry

_DAYS_TR = {
    0: "Pazartesi",
    1: "Salı",
    2: "Çarşamba",
    3: "Perşembe",
    4: "Cuma",
    5: "Cumartesi",
    6: "Pazar",
}

_MONTHS_TR = {
    1: "Ocak",
    2: "Şubat",
    3: "Mart",
    4: "Nisan",
    5: "Mayıs",
    6: "Haziran",
    7: "Temmuz",
    8: "Ağustos",
    9: "Eylül",
    10: "Ekim",
    11: "Kasım",
    12: "Aralık",
}


def get_current_time(timezone: str = "Europe/Istanbul") -> str:
    """Return the current date, time, and day of week."""
    try:
        tz = zoneinfo.ZoneInfo(timezone)
    except Exception:
        tz = zoneinfo.ZoneInfo("Europe/Istanbul")
        timezone = "Europe/Istanbul"

    now = datetime.now(tz)
    day_name = _DAYS_TR.get(now.weekday(), "")
    month_name = _MONTHS_TR.get(now.month, "")

    formatted_date = f"{now.day} {month_name} {now.year} {day_name}"
    formatted_time = now.strftime("%H:%M:%S")
    offset = now.strftime("%z")

    return f"Tarih ve Saat ({timezone}): {formatted_date}, {formatted_time} (UTC{offset[:3]}:{offset[3:]})"


TIME_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "Get the current real-time date, time, and day of the week. Always use this when asked about the current time, date, today's day of week, or time differences.",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone string (default: 'Europe/Istanbul')",
                    "default": "Europe/Istanbul",
                }
            },
        },
    },
}

TOOL_NAME = "get_current_time"
TOOL_ENTRY = ToolEntry(
    schema=TIME_SCHEMA,
    fn=get_current_time,
)
