from __future__ import annotations

from datetime import datetime
import logging
import time
from typing import Any

import discord

from bot.llm.errors import parse_error_message
_last_notifications = {}


async def notify_admin_error(
    discord_bot: discord.Client,
    config: dict[str, Any],
    error: Exception,
    context: str = "",
) -> None:
    """
    Send a concise error notification to all configured admins.
    """
    try:
        key = (type(error).__name__, context)
        now = time.monotonic()
        if now - _last_notifications.get(key, -600) < 300:
            return
        _last_notifications[key] = now
        if len(_last_notifications) > 100:
            _last_notifications.clear()
        admin_ids = config.get("permissions", {}).get("users", {}).get("admin_ids", [])
        if not admin_ids:
            return

        msg = (
            "🤖 **Bot hata bildirimi**\n"
            f"⏰ Zaman: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📝 Bağlam: {context}\n\nHata: {parse_error_message(error)}"
        )
        for admin_id in admin_ids:
            try:
                user = discord_bot.get_user(admin_id) or await discord_bot.fetch_user(
                    admin_id
                )
                await user.send(msg)
            except Exception as e:  # noqa: BLE001
                logging.warning("Could not notify admin %s: %s", admin_id, e)
    except Exception as e:  # noqa: BLE001
        logging.warning("Failed to notify admins: %s", e)


async def handle_app_command_error(
    interaction: discord.Interaction,
    error: Exception,
    discord_bot: discord.Client,
    config: dict[str, Any],
) -> None:
    """
    Standard handler for slash command errors.
    """
    from discord import app_commands
    if isinstance(error, app_commands.CheckFailure):
        msg = str(error) if str(error) else "Bu komutu veya DM etkileşimini kullanma yetkiniz yok."
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ {msg}", ephemeral=True)
        except Exception:
            pass
        return

    logging.exception("App command error: %s", error)
    await notify_admin_error(
        discord_bot,
        config,
        error,
        f"App command error: {getattr(interaction.command, 'name', 'unknown')}",
    )
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Komut yürütülürken bir hata oluştu.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "Komut yürütülürken bir hata oluştu.", ephemeral=True
            )
    except Exception:  # noqa: BLE001
        pass

