"""Small deterministic recognizers for common natural-language bot actions."""
from __future__ import annotations

import unicodedata


def normalize_intent_text(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.casefold()).replace("ı", "i")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(value.split())


def trivia_intent(text: str) -> str | None:
    """Return 'leaderboard', 'play', or None for clear natural-language trivia requests."""
    value = normalize_intent_text(text)
    has_trivia_context = any(term in value for term in ("kimdedi", "kim dedi", "trivia"))
    if not has_trivia_context:
        return None

    if any(term in value for term in (
        "siralama", "liderlik", "skor tablosu", "puan tablosu", "skorlar", "puanlar",
    )):
        return "leaderboard"
    if any(term in value for term in (
        "oyna", "oynatsana", "oynatalim", "oynayalim", "oyunu kur",
        "baslat", "baslatsana", "baslatalim", "soru sor", "soru at",
    )):
        return "play"
    return None
