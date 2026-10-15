"""
Equicord Quoter-style quote card renderer for Discord AI bot.
Generates 1200x600 PNG images:
- Left: 600x600 avatar with smooth gradient fade into black from x=200..600
- Right: Centered elegant quote typography (M PLUS Rounded 1c Light) and author credit
"""
from __future__ import annotations

import io
import logging
import os
import re
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

CANVAS_WIDTH = 1200
CANVAS_HEIGHT = 600
AVATAR_SIZE = 600
FADE_START_X = 200
FADE_END_X = 600

TEXT_AREA_X = 620
TEXT_AREA_WIDTH = 520
MAX_TEXT_HEIGHT = 480

_PRIMARY_FONT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "assets",
    "fonts",
    "MPLUSRounded1c-Light.ttf",
)

FALLBACK_FONTS = [
    _PRIMARY_FONT_PATH,
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "DejaVuSans.ttf",
    "Arial.ttf",
]


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FALLBACK_FONTS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()


def clean_discord_text(text: str) -> str:
    """Clean custom Discord emojis and formatting for quotes."""
    # Convert <:name:id> or <a:name:id> to :name:
    clean = re.sub(r"<a?:([a-zA-Z0-9_]+):[0-9]+>", r":\1:", text)
    # Remove bot mention prefix if it was included in text
    clean = re.sub(r"^<@!?[0-9]+>\s*", "", clean).strip()
    return clean


def render_quote_card(
    text: str,
    author: str,
    avatar_bytes: Optional[bytes] = None,
) -> bytes:
    """
    Renders an Equicord Quoter style quote card.

    Args:
        text: Quoted message text
        author: Author display name
        avatar_bytes: Raw PNG/JPG bytes of the author's avatar

    Returns:
        PNG image bytes
    """
    clean_text = clean_discord_text(text)
    if not clean_text:
        clean_text = "(boş mesaj)"

    # Enclose in typographical quotes if not already quoted
    if not (clean_text.startswith('"') or clean_text.startswith('“') or clean_text.startswith('«')):
        display_quote = f"“{clean_text}”"
    else:
        display_quote = clean_text

    clean_author = f"- {author.strip()}" if author.strip() else "- Anonim"

    # Base canvas (Pitch Black)
    base = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 255))

    # 1. Avatar rendering with fade
    avatar_img = None
    if avatar_bytes:
        try:
            avatar_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
        except Exception as e:
            logger.warning("Failed to decode avatar bytes: %s", e)

    if avatar_img:
        # Crop square from center
        min_dim = min(avatar_img.width, avatar_img.height)
        left = (avatar_img.width - min_dim) // 2
        top = (avatar_img.height - min_dim) // 2
        avatar_cropped = avatar_img.crop((left, top, left + min_dim, top + min_dim))
        avatar_resized = avatar_cropped.resize((AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)

        # Gradient mask: 0..200 full opaque, 200..600 linear drop to 0
        mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 255)
        mask_pixels = mask.load()
        for x in range(AVATAR_SIZE):
            if x < FADE_START_X:
                alpha = 255
            else:
                ratio = (x - FADE_START_X) / float(FADE_END_X - FADE_START_X)
                alpha = int(255 * (1.0 - min(max(ratio, 0.0), 1.0)))
            for y in range(AVATAR_SIZE):
                mask_pixels[x, y] = alpha

        avatar_resized.putalpha(mask)
        base.paste(avatar_resized, (0, 0), avatar_resized)
    else:
        # Fallback monogram circle / initial
        draw_temp = ImageDraw.Draw(base)
        initial = (author.strip()[:1] or "?").upper()
        init_font = _get_font(160)
        bbox = draw_temp.textbbox((0, 0), initial, font=init_font)
        iw = bbox[2] - bbox[0]
        ih = bbox[3] - bbox[1]
        draw_temp.text((180 - iw // 2, 300 - ih // 2), initial, font=init_font, fill=(70, 70, 70, 255))

    # 2. Text layout & wrap
    draw = ImageDraw.Draw(base)

    best_q_font = None
    best_a_font = None
    best_lines: list[str] = []
    best_line_height = 0

    # Dynamically find the best font size (42 down to 18)
    for size in range(42, 16, -2):
        q_font = _get_font(size)
        a_font = _get_font(max(int(size * 0.62), 18))

        # Wrap text based on pixel width
        lines = []
        for paragraph in display_quote.split("\n"):
            p_clean = paragraph.strip()
            if not p_clean:
                continue
            words = p_clean.split()
            cur_line: list[str] = []
            for w in words:
                candidate = " ".join(cur_line + [w]) if cur_line else w
                tb = draw.textbbox((0, 0), candidate, font=q_font)
                if (tb[2] - tb[0]) <= TEXT_AREA_WIDTH:
                    cur_line.append(w)
                else:
                    if cur_line:
                        lines.append(" ".join(cur_line))
                        cur_line = [w]
                    else:
                        lines.append(w)
            if cur_line:
                lines.append(" ".join(cur_line))

        line_h = int(size * 1.38)
        text_block_h = len(lines) * line_h
        a_bbox = draw.textbbox((0, 0), clean_author, font=a_font)
        author_h = a_bbox[3] - a_bbox[1]

        total_block_h = text_block_h + 35 + author_h
        if total_block_h <= MAX_TEXT_HEIGHT:
            best_q_font = q_font
            best_a_font = a_font
            best_lines = lines
            best_line_height = line_h
            break

    if not best_lines:
        best_q_font = _get_font(18)
        best_a_font = _get_font(16)
        best_lines = [display_quote[:120] + "..."]
        best_line_height = 26

    # 3. Center and draw
    a_bbox = draw.textbbox((0, 0), clean_author, font=best_a_font)
    author_w = a_bbox[2] - a_bbox[0]
    author_h = a_bbox[3] - a_bbox[1]

    total_content_h = len(best_lines) * best_line_height + 35 + author_h
    start_y = (CANVAS_HEIGHT - total_content_h) // 2

    # Draw quote lines
    curr_y = start_y
    for line in best_lines:
        tb = draw.textbbox((0, 0), line, font=best_q_font)
        lw = tb[2] - tb[0]
        lx = TEXT_AREA_X + (TEXT_AREA_WIDTH - lw) // 2
        draw.text((lx, curr_y), line, font=best_q_font, fill=(255, 255, 255, 255))
        curr_y += best_line_height

    # Draw author line
    curr_y += 35
    ax = TEXT_AREA_X + (TEXT_AREA_WIDTH - author_w) // 2
    draw.text((ax, curr_y), clean_author, font=best_a_font, fill=(190, 190, 190, 255))

    out_buf = io.BytesIO()
    base.save(out_buf, format="PNG", optimize=True)
    return out_buf.getvalue()
