"""
Web search tool with hybrid support:
- Uses Brave Search API when BRAVE_API_KEY is configured.
- Seamlessly falls back to DuckDuckGo search (via ddgs) with zero API keys required.

Use web_search when:
- The user asks for up-to-date, recent, or real-time information.
- The answer may change over time (e.g., news, sports, releases, events).
- You need external information not in your training data.
"""

from __future__ import annotations

import logging
import os
import threading as _threading
import time
from typing import Any

import requests

from bot.llm.tools._types import ToolEntry

# ── Brave API ─────────────────────────────────────────────────────────────────

_BRAVE_LAST_CALL: float = 0.0
_BRAVE_MIN_INTERVAL: float = 1.2  # free tier: 1 req/s
_BRAVE_LOCK = _threading.Lock()  # serialise concurrent calls


def brave_web_search(query: str) -> dict:
    """Call Brave Search API and return the raw JSON response."""
    global _BRAVE_LAST_CALL
    with _BRAVE_LOCK:
        elapsed = time.monotonic() - _BRAVE_LAST_CALL
        if elapsed < _BRAVE_MIN_INTERVAL:
            time.sleep(_BRAVE_MIN_INTERVAL - elapsed)
        api_key = os.getenv("BRAVE_API_KEY") or os.getenv("brave_api_key")
        if not api_key:
            raise ValueError("Brave API key missing")
        response = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 8},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            },
            timeout=10,
        )
        response.raise_for_status()
        _BRAVE_LAST_CALL = time.monotonic()
        return response.json()


# ── DuckDuckGo Search (Zero API Key Fallback) ────────────────────────────────

def duckduckgo_search(query: str, max_results: int = 8) -> dict:
    """Search DuckDuckGo using ddgs package."""
    try:
        from ddgs import DDGS
        results = list(DDGS().text(query, max_results=max_results))
        formatted = []
        for r in results:
            formatted.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "description": r.get("body", ""),
            })
        return {"web": {"results": formatted}, "_provider": "duckduckgo"}
    except Exception as e:
        logging.warning("DuckDuckGo search error: %s", e)
        # Secondary fallback if DDGS fails: try HTML instant answers or report error
        return {"web": {"results": []}, "error": str(e)}


# ── Unified Hybrid Search ────────────────────────────────────────────────────

def web_search(query: str) -> dict:
    """
    Search the web. Tries Brave API first if key configured;
    falls back cleanly to DuckDuckGo if key is absent or request fails.
    """
    api_key = os.getenv("BRAVE_API_KEY") or os.getenv("brave_api_key")
    if api_key:
        try:
            return brave_web_search(query)
        except Exception as e:
            logging.warning("Brave search failed, falling back to DuckDuckGo: %s", e)

    return duckduckgo_search(query)


# ── Formatters ────────────────────────────────────────────────────────────────

def format_search_results(result: dict, user_search: str) -> str:
    """Format API response into clean text for the model."""
    results = result.get("web", {}).get("results", [])
    if not results:
        err = result.get("error")
        if err:
            return f"Arama yapılırken bir hata oluştu: {err}"
        return f"'{user_search}' için internette arama sonucu bulunamadı."

    output: list[str] = [f'Search results for "{user_search}":']
    for r in results:
        title = r.get("title", "")
        url = r.get("url", "")
        desc = r.get("description", "")
        output.append(title or url)
        if url:
            output.append(f"   URL: {url}")
        if desc:
            output.append(f"   Content: {desc}")
        output.append("")
    return "\n".join(output).rstrip()


def _fmt_search(result: dict, args: dict) -> str:
    """Wrapper for dynamic tool registration."""
    return format_search_results(result, user_search=args.get("query", ""))


# Backward compatibility alias
format_brave_results = format_search_results


# ── Schema ───────────────────────────────────────────────────────────────────

WEB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for up-to-date, recent, or real-time information, news, current events, and websites.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"}
            },
            "required": ["query"],
        },
    },
}

TOOL_NAME = "web_search"
TOOL_ENTRY = ToolEntry(
    schema=WEB_SEARCH_SCHEMA,
    fn=web_search,
    formatter=_fmt_search,
)
