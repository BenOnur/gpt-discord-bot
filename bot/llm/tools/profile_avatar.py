"""Send a server member's highest-resolution Discord profile avatar."""
from __future__ import annotations

import io
import logging
import re
import unicodedata
from typing import Any

import discord

from bot.llm.tools._types import ToolEntry
from bot.llm.tools.discord_ctx import get_current_discord_context

logger = logging.getLogger(__name__)


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"<@!?\d+>", " ", value)
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def looks_like_avatar_request(text: str) -> bool:
    """Recognize explicit requests to send/show someone's profile image."""
    normalized = _normalize(text)
    mentions_image = any(term in normalized for term in (
        "profil fotograf", "profil resmi", "profil fotosu", "avatar",
    ))
    asks_to_send = any(term in normalized for term in (
        "gonder", "gondersene", "yolla", "yollasana", "at", "atsana",
        "goster", "gostersene", "paylas", "getir",
    ))
    return mentions_image and asks_to_send


def _resolve_member(target: str | None, ctx: dict[str, Any]):
    guild = ctx.get("guild")
    message = ctx.get("message")
    bot = ctx.get("bot")
    if guild is None:
        return None, "Profil fotoğrafını yalnız sunucu içinden bulup gönderebilirim."

    bot_id = getattr(getattr(bot, "user", None), "id", None)
    mentions = [member for member in getattr(message, "mentions", ()) if member.id != bot_id]
    if len(mentions) == 1:
        return mentions[0], None
    if len(mentions) > 1:
        query = _normalize(target or "")
        exact = [m for m in mentions if query and query in {
            _normalize(m.name), _normalize(m.display_name),
            _normalize(getattr(m, "global_name", "") or ""),
        }]
        if len(exact) == 1:
            return exact[0], None
        return None, "Birden fazla kişi etiketlenmiş. Profil fotoğrafını istediğin kişiyi tek başına etiketle."

    query = _normalize(target or "")
    if not query:
        content = _normalize(getattr(message, "content", ""))
        if any(word in content.split() for word in ("ben", "benim", "kendim", "kendimin")):
            author = ctx.get("author")
            if author is not None:
                return author, None
        # Match a member's complete username/display name in the original request.
        candidates = []
        for member in getattr(guild, "members", ()):
            names = (member.name, member.display_name, getattr(member, "global_name", None) or "")
            for name in names:
                normalized_name = _normalize(name)
                if normalized_name and re.search(rf"(?<!\w){re.escape(normalized_name)}(?!\w)", content):
                    candidates.append(member)
                    break
        unique = {m.id: m for m in candidates}
        if len(unique) == 1:
            return next(iter(unique.values())), None
        if len(unique) > 1:
            return None, "Birden fazla üyeyle eşleşti. İstediğin kişiyi etiketle."
        return None, "Kimin profil fotoğrafını istediğini bulamadım. O kişiyi etiketle ya da adını yaz."

    if query.isdigit():
        member = guild.get_member(int(query))
        if member is not None:
            return member, None

    matches = []
    for member in getattr(guild, "members", ()):
        names = (member.name, member.display_name, getattr(member, "global_name", None) or "")
        if query in {_normalize(name) for name in names if name}:
            matches.append(member)
    matches = list({m.id: m for m in matches}.values())

    # Onur is the server owner in this server; use that only when no exact name matched.
    if not matches and query in {"onur", "onur un", "onurun"}:
        owner = guild.get_member(guild.owner_id)
        if owner is not None:
            return owner, None
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, f"“{target}” adı birden fazla üyeyle eşleşiyor. İstediğin kişiyi etiketle."
    return None, f"“{target}” adlı üyeyi bu sunucuda bulamadım."


async def _async_send_profile_avatar(target: str | None, ctx: dict[str, Any]) -> str:
    channel = ctx.get("channel")
    message = ctx.get("message")
    member, error = _resolve_member(target, ctx)
    if error:
        if channel:
            await channel.send(error)
            ctx["suppress_chat_reply"] = True
            ctx["suppress_reaction"] = "❓"
        return error
    if channel is None:
        return "Discord kanal bağlamı bulunamadı."

    asset = member.display_avatar
    image_format = "gif" if asset.is_animated() else "png"
    try:
        image_bytes = await asset.with_format(image_format).with_size(4096).read()
        filename = f"{re.sub(r'[^A-Za-z0-9_-]+', '_', member.display_name).strip('_') or 'member'}_avatar.{image_format}"
        file = discord.File(io.BytesIO(image_bytes), filename=filename)
        await channel.send(
            content=f"İşte {member.display_name}’in yüksek çözünürlüklü profil fotoğrafı.",
            file=file,
            reference=message.to_reference(fail_if_not_exists=False) if message else None,
            mention_author=False,
        )
        ctx["suppress_chat_reply"] = True
        ctx["suppress_reaction"] = "📷"
        return f"{member.display_name} için 4096 px profil fotoğrafı kanala gönderildi."
    except Exception as exc:
        logger.exception("Could not send profile avatar for member %s", member.id)
        try:
            fallback_asset = asset.with_format(image_format).with_size(4096)
            embed = discord.Embed(color=discord.Color.blurple())
            embed.set_image(url=fallback_asset.url)
            await channel.send(
                content=f"{member.display_name}’in yüksek çözünürlüklü fotoğrafı burada.",
                embed=embed,
                reference=message.to_reference(fail_if_not_exists=False) if message else None,
                mention_author=False,
            )
            ctx["suppress_chat_reply"] = True
            ctx["suppress_reaction"] = "📷"
            return f"{member.display_name} için 4096 px profil fotoğrafı önizlemesi gönderildi."
        except Exception:
            logger.exception("Could not send profile avatar CDN fallback for member %s", member.id)
            try:
                await channel.send(f"{member.display_name}’in profil fotoğrafını Discord’a gönderemedim.")
                ctx["suppress_chat_reply"] = True
                ctx["suppress_reaction"] = "❓"
            except Exception:
                pass
        return f"Profil fotoğrafı gönderilemedi: {type(exc).__name__}."


def send_profile_avatar(target: str | None = None) -> str:
    """Tool entry point; safely sends the requested member's avatar to this channel."""
    import asyncio

    ctx = get_current_discord_context()
    bot = ctx.get("bot")
    if not bot or not hasattr(bot, "loop") or not bot.loop.is_running():
        return "Profil fotoğrafı göndermek için etkin bir Discord mesajı gerekli."
    future = asyncio.run_coroutine_threadsafe(_async_send_profile_avatar(target, ctx), bot.loop)
    try:
        return future.result(timeout=20.0)
    except Exception as exc:
        logger.exception("send_profile_avatar failed")
        return f"Profil fotoğrafı gönderilirken hata oluştu: {type(exc).__name__}."


PROFILE_AVATAR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_profile_avatar",
        "description": "Send a server member's original Discord display avatar to the current channel at the highest CDN size (4096 px; preserves animated GIF). Use whenever someone asks in natural language to send/show a user's profile photo, profile picture, or avatar. Prefer an actually tagged member; otherwise resolve the exact member name. This tool itself sends the image with a short caption.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Exact Discord username/display name or user ID. Omit when the desired person is actually tagged in the triggering message or is the message author.",
                }
            },
        },
    },
}

TOOL_NAME = "send_profile_avatar"
TOOL_ENTRY = ToolEntry(schema=PROFILE_AVATAR_SCHEMA, fn=send_profile_avatar)
