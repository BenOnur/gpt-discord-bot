"""
Context storage for Discord interaction objects (message, channel, author, bot, scheduler).
Allows tools to safely access Discord interaction state.
"""
from __future__ import annotations

import contextvars
from typing import Any, Dict

_discord_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "_discord_context", default={}
)


def set_current_discord_context(ctx: Dict[str, Any]) -> None:
    """Store current Discord context for the active async task."""
    _discord_context.set(ctx)


def get_current_discord_context() -> Dict[str, Any]:
    """Retrieve current Discord context."""
    return _discord_context.get()
