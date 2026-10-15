"""
URL content reader for web pages, news articles, tweets (X), and YouTube videos.
Extracts clean, readable text without boilerplate HTML.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup
import requests

from bot.llm.tools._types import ToolEntry

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _read_tweet(url: str) -> str | None:
    """Extract tweet text via Twitter's oEmbed endpoint without API keys."""
    try:
        oembed_url = f"https://publish.twitter.com/oembed?url={urllib.parse.quote(url)}"
        resp = requests.get(oembed_url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            author = data.get("author_name", "")
            html = data.get("html", "")
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(separator=" ").strip()
            return f"🐦 Tweet ({author}):\n{text}"
    except Exception as e:
        logging.warning("Twitter oEmbed failed for %s: %s", url, e)
    return None


def _read_youtube(url: str) -> str | None:
    """Extract YouTube title and description via oEmbed."""
    try:
        oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json"
        resp = requests.get(oembed_url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            title = data.get("title", "")
            author = data.get("author_name", "")
            return f"📺 YouTube Videosu: {title}\nKanal: {author}\nLink: {url}"
    except Exception as e:
        logging.warning("YouTube oEmbed failed for %s: %s", url, e)
    return None


def read_url(url: str) -> str:
    """Fetch and extract readable text from a URL."""
    clean_url = url.strip()
    if not clean_url.startswith(("http://", "https://")):
        clean_url = "https://" + clean_url

    # Check for Twitter / X
    if re.search(r"(twitter\.com|x\.com)/.+/status/\d+", clean_url):
        tweet_text = _read_tweet(clean_url)
        if tweet_text:
            return tweet_text

    # Check for YouTube
    if re.search(r"(youtube\.com/watch|youtu\.be/)", clean_url):
        yt_text = _read_youtube(clean_url)
        if yt_text:
            return yt_text

    # General web page / article
    try:
        resp = requests.get(clean_url, headers=_HEADERS, timeout=12)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.content, "html.parser")

        # Strip scripts, styles, navigation, footer
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        # Extract meta description
        meta_desc = ""
        meta_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        if meta_tag and meta_tag.get("content"):
            meta_desc = meta_tag["content"].strip()

        # Find main content container if possible
        main = soup.find("article") or soup.find("main") or soup.find("div", class_=re.compile(r"(content|article|post|entry|text)", re.I)) or soup.body

        text = ""
        if main:
            paragraphs = main.find_all(["p", "h1", "h2", "h3", "li"])
            text_lines = [p.get_text(separator=" ").strip() for p in paragraphs if len(p.get_text().strip()) > 20]
            text = "\n\n".join(text_lines)

        if not text:
            text = soup.get_text(separator="\n", strip=True)

        # Truncate to reasonable token length (~4000 chars)
        clean_text = re.sub(r"\n{3,}", "\n\n", text).strip()[:4000]

        lines = [f"🌐 Web Sayfası: {title or clean_url}"]
        if meta_desc:
            lines.append(f"Açıklama: {meta_desc}")
        lines.append(f"URL: {clean_url}")
        lines.append("\nİçerik:")
        lines.append(clean_text or "(Sayfada okunabilir metin bulunamadı)")

        return "\n".join(lines)
    except requests.exceptions.RequestException as e:
        return f"Sayfa okunamadı ({clean_url}): {e}"
    except Exception as e:
        return f"Sayfa işlenirken hata oluştu ({clean_url}): {e}"


READ_URL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_url",
        "description": "Fetch and extract text content from a web page, news article, tweet (X), or YouTube link. Use this when a user shares a URL or asks about a link's contents.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The web URL to fetch and read (e.g. 'https://...', 'https://x.com/...', 'https://youtu.be/...')"
                }
            },
            "required": ["url"],
        },
    },
}

TOOL_NAME = "read_url"
TOOL_ENTRY = ToolEntry(
    schema=READ_URL_SCHEMA,
    fn=read_url,
)
