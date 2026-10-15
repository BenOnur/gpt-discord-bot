"""
Wikipedia article summary and search tool.
No API key required.
"""
from __future__ import annotations

import logging
import urllib.parse
import requests

from bot.llm.tools._types import ToolEntry


def wikipedia_search(query: str, lang: str = "tr") -> str:
    """Search Wikipedia for an article and return its summary."""
    clean_query = query.strip()
    if not clean_query:
        return "Arama için bir konu veya kelime belirtilmedi."

    headers = {
        "User-Agent": "DiscordAIBot/1.0 (https://github.com/BenOnur/gpt-discord-bot)"
    }

    # First try direct REST API summary
    encoded_title = urllib.parse.quote(clean_query.replace(" ", "_"))
    summary_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded_title}"
    try:
        resp = requests.get(summary_url, headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            title = data.get("title", clean_query)
            description = data.get("description", "")
            extract = data.get("extract", "")
            page_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
            lines = [f"📖 Wikipedia: {title}"]
            if description:
                lines.append(f"Tanım: {description}")
            if extract:
                lines.append(f"Özet: {extract}")
            if page_url:
                lines.append(f"Kaynak: {page_url}")
            return "\n".join(lines)
    except Exception as e:
        logging.warning("Wikipedia summary lookup failed: %s", e)

    # Fallback to search API
    search_url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "opensearch",
        "search": clean_query,
        "limit": 3,
        "namespace": 0,
        "format": "json",
    }
    try:
        resp = requests.get(search_url, params=params, headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if len(data) >= 4 and data[1]:
                titles = data[1]
                descriptions = data[2]
                urls = data[3]

                # Try fetching the first matched article's summary
                first_title = urllib.parse.quote(titles[0].replace(" ", "_"))
                first_summary_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{first_title}"
                first_resp = requests.get(first_summary_url, headers=headers, timeout=8)
                if first_resp.status_code == 200:
                    first_data = first_resp.json()
                    extract = first_data.get("extract", "")
                    page_url = first_data.get("content_urls", {}).get("desktop", {}).get("page", urls[0])
                    lines = [f"📖 Wikipedia: {first_data.get('title', titles[0])}"]
                    if first_data.get("description"):
                        lines.append(f"Tanım: {first_data.get('description')}")
                    if extract:
                        lines.append(f"Özet: {extract}")
                    lines.append(f"Kaynak: {page_url}")
                    return "\n".join(lines)

                lines = [f"📖 Wikipedia Arama Sonuçları ('{clean_query}'):"]
                for t, d, u in zip(titles, descriptions, urls):
                    lines.append(f"- **{t}**: {d} ({u})")
                return "\n".join(lines)
        return f"Wikipedia'da '{clean_query}' ile ilgili makale bulunamadı."
    except Exception as e:
        return f"Wikipedia arama hatası: {e}"


WIKIPEDIA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "wikipedia_search",
        "description": "Search Wikipedia and retrieve detailed summaries of people, history, concepts, games, lore, science, places, and events.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search term or article title to look up on Wikipedia",
                },
                "lang": {
                    "type": "string",
                    "description": "Language code (default: 'tr' for Turkish, 'en' for English)",
                    "default": "tr",
                },
            },
            "required": ["query"],
        },
    },
}

TOOL_NAME = "wikipedia_search"
TOOL_ENTRY = ToolEntry(
    schema=WIKIPEDIA_SCHEMA,
    fn=wikipedia_search,
)
