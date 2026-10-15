#!/usr/bin/env python3
"""
Interactive Telegram Backup Bot Daemon.
Listens for commands from authorized Telegram user (Onur):
- /backup discord (or /backup): takes an atomic snapshot of Discord bot memory and uploads it
- /status: checks Discord bot service status, uptime and last backup info
- /ping: responds with pong
- /help: lists available commands
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os
from pathlib import Path
import subprocess
import sys

import httpx

# Add scripts directory to path for imports
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from telegram_backup import load_env, execute_backup, BACKUP_STORE_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backup_bot_daemon")


async def send_message(client: httpx.AsyncClient, token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        await client.post(url, json=payload, timeout=15.0)
    except Exception as e:
        logger.error("Failed to send message: %s", e)


def get_service_status() -> str:
    try:
        res = subprocess.run(["systemctl", "is-active", "discord-ai-bot.service"], capture_output=True, text=True)
        bot_status = res.stdout.strip()
    except Exception:
        bot_status = "unknown"

    # Last backup info
    last_backup_info = "Henüz yerel yedek bulunmuyor"
    if BACKUP_STORE_DIR.exists():
        files = sorted(BACKUP_STORE_DIR.glob("discord-memory-*.tar.gz"), key=lambda p: p.stat().st_mtime)
        if files:
            last = files[-1]
            size_mb = last.stat().st_size / (1024 * 1024)
            mtime = datetime.fromtimestamp(last.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            last_backup_info = f"<code>{last.name}</code> ({size_mb:.2f} MB, {mtime})"

    return (
        f"📊 <b>VDS & Discord Bot Durumu</b>\n\n"
        f"• Discord Bot Servisi: <b>{'🟢 Aktif (running)' if bot_status == 'active' else '🔴 ' + bot_status}</b>\n"
        f"• Son Yedek: {last_backup_info}\n"
        f"• Otomatik Zamanlayıcı: <b>Her gece 04:00 TRT</b>\n"
        f"• Anlık Manuel Yedek: <code>/backup discord</code>"
    )


async def handle_update(client: httpx.AsyncClient, token: str, authorized_chat_id: str, update: dict) -> None:
    message = update.get("message")
    if not message:
        return

    from_user = message.get("from", {})
    user_id = str(from_user.get("id"))
    raw_text = message.get("text", "").strip()

    # Security check: only authorized user can issue commands
    if user_id != authorized_chat_id:
        logger.warning("Unauthorized access attempt from user_id=%s username=%s: %s", user_id, from_user.get("username"), raw_text)
        await send_message(client, token, user_id, "⛔ Yetkisiz erişim. Bu bot yalnızca sunucu yöneticisine tahsis edilmiştir.")
        return

    text_lower = raw_text.lower()

    if text_lower in ("/start", "/help"):
        help_msg = (
            f"👋 Selam Onur! <b>Backup Botu</b> devrede.\n\n"
            f"<b>Kullanabileceğin Komutlar:</b>\n"
            f"• <code>/backup discord</code> (veya <code>/backup</code>) - Discord hafızasının canlı anlık yedeğini hemen alır ve buraya gönderir.\n"
            f"• <code>/status</code> - Discord botunun ve son yedeğin durumunu kontrol eder.\n"
            f"• <code>/ping</code> - Botun çalıştığını test eder.\n\n"
            f"<i>Not: Ayrıca her gece 04:00'te otomatik günlük yedek de bu sohbete iletilir.</i>"
        )
        await send_message(client, token, authorized_chat_id, help_msg)

    elif text_lower == "/ping":
        await send_message(client, token, authorized_chat_id, "🏓 Pong! Backup botu VDS'te tıkır tıkır çalışıyor.")

    elif text_lower == "/status":
        status_msg = get_service_status()
        await send_message(client, token, authorized_chat_id, status_msg)

    elif text_lower.startswith("/backup"):
        target = raw_text[len("/backup"):].strip().lower()
        if not target or "discord" in target:
            await send_message(
                client,
                token,
                authorized_chat_id,
                "⏳ <b>Discord AI Bot hafıza yedeği hazırlanıyor...</b>\nAtomik SQLite snapshot alınıyor, lütfen birkaç saniye bekle.",
            )
            try:
                # Run the backup in a separate thread so event loop is not blocked
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: execute_backup(trigger_name="Manuel Komut (/backup discord)"))
            except Exception as e:
                logger.error("Manuel backup error: %s", e)
                await send_message(client, token, authorized_chat_id, f"❌ Yedekleme sırasında hata oluştu:\n<code>{e}</code>")
        else:
            await send_message(client, token, authorized_chat_id, f"Bilinmeyen yedek hedefi: <code>{target}</code>. Kullanım: <code>/backup discord</code>")

    else:
        await send_message(client, token, authorized_chat_id, "Bilinmeyen komut. Kullanabileceğin komutları görmek için: /help")


async def run_bot() -> None:
    token, authorized_chat_id = load_env()
    logger.info("Starting Telegram Backup Bot daemon for authorized chat_id=%s...", authorized_chat_id)

    offset = 0
    poll_url = f"https://api.telegram.org/bot{token}/getUpdates"

    async with httpx.AsyncClient(timeout=45.0) as client:
        while True:
            try:
                params = {"timeout": 30}
                if offset:
                    params["offset"] = offset

                resp = await client.get(poll_url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        offset = max(offset, update["update_id"] + 1)
                        await handle_update(client, token, authorized_chat_id, update)
                else:
                    logger.warning("getUpdates returned status %d: %s", resp.status_code, resp.text)
                    await asyncio.sleep(3)

            except httpx.TimeoutException:
                # Standard long polling timeout, just loop again
                continue
            except asyncio.CancelledError:
                logger.info("Daemon cancelled, shutting down.")
                break
            except Exception as e:
                logger.error("Unexpected error in poll loop: %s", e)
                await asyncio.sleep(5)


def main() -> int:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Exiting on KeyboardInterrupt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
