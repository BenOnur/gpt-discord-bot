"""
bot/trivia module - 'Bunu Kim Söyledi?' Server Message Trivia.
"""
from .game import (
    get_random_trivia,
    record_trivia_win,
    get_trivia_leaderboard,
    TriviaView,
)

__all__ = [
    "get_random_trivia",
    "record_trivia_win",
    "get_trivia_leaderboard",
    "TriviaView",
]
