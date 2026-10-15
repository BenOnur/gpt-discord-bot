#!/usr/bin/env python3
"""
Interactive Telegram Backup Bot & Puppet Controller Daemon.
Listens for commands from authorized Telegram user (Onur):
- /backup discord (or /backup): takes an atomic snapshot of Discord bot memory and uploads it
- /say [channel] <text>: sends message to Discord as the bot (Puppet Mode)
- /tts [channel] <text>: joins voice channel, speaks aloud in male neural voice, and leaves
- /channels: lists all text and voice channels with active members
- /restart discord: restarts the Discord bot systemd service
- /status: checks Discord bot service status and last backup info
- /ping: responds with pong
- /help: lists available commands
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
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

IPC_HOST = "127.0.0.1"
IPC_PORT = 8799


async def call_ipc(req: dict) -> dict:
    """Send JSON request to local Discord bot IPC server on port 8799."""
    try:
        reader, writer = await asyncio.open_connection(IPC_HOST, IPC_PORT)
        writer.write(json.dumps(req).encode("utf-8") + b"\n")
        await writer.drain()
        line = await reader.readline()
        writer.close()
        await writer.wait_closed()
        if not line:
            return {"ok": False, "error": "IPC sunucusundan boş yanıt alındı"}
        return json.loads(line.decode("utf-8").strip())
    except ConnectionRefusedError:
        return {"ok": False, "error": "Discord bot IPC servisi çevrimdışı (bot kapalı veya yeniden başlıyor olabilir)"}
    except Exception as e:
        return {"ok": False, "error": f"IPC bağlantı hatası: {e}"}


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
        f"• Discord Bot: <b>{'🟢 Aktif (running)' if bot_status == 'active' else '🔴 ' + bot_status}</b>\n"
        f"• Son Yedek: {last_backup_info}\n"
        f"• Zamanlayıcı: <b>Her gece 04:00 TRT</b>\n"
        f"• Kukla Modu (IPC): <b>127.0.0.1:8799</b>"
    )


BOT_COMMANDS = [
    {"command": "say", "description": "Discord kanala bot olarak mesaj at"},
    {"command": "tts", "description": "Ses kanalina gir, erkek sesiyle soyle"},
    {"command": "channels", "description": "Discord kanallari ve sestekileri goster"},
    {"command": "backup", "description": "Canli Discord hafiza yedegi al ve gonder"},
    {"command": "status", "description": "Bot ve yedekleme durumu"},
    {"command": "restart", "description": "Discord botunu yeniden baslat"},
    {"command": "ping", "description": "Bot baglanti testi"},
    {"command": "help", "description": "Komut yardimi ve aciklamalar"},
]


async def register_bot_commands(client: httpx.AsyncClient, token: str) -> None:
    """Register command autocompletion menu with Telegram Bot API."""
    try:
        await client.post(
            f"https://api.telegram.org/bot{token}/setMyCommands",
            json={"commands": BOT_COMMANDS},
            timeout=10.0,
        )
        await client.post(
            f"https://api.telegram.org/bot{token}/setChatMenuButton",
            json={"menu_button": {"type": "commands"}},
            timeout=10.0,
        )
        logger.info("Telegram bot commands & menu button registered successfully.")
    except Exception as e:
        logger.warning("Failed to register Telegram bot commands: %s", e)


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

    raw_parts = raw_text.split(None, 1)
    cmd = raw_parts[0].lower().split("@")[0] if raw_parts else ""
    rest = raw_parts[1].strip() if len(raw_parts) > 1 else ""

    if cmd in ("/start", "/help"):
        help_msg = (
            f"👋 Selam Onur! <b>Backup & Puppet Botu</b> devrede.\n\n"
            f"<b>🎭 Discord Kukla Komutları (Gizli Trollük):</b>\n"
            f"• <code>/say mesajınız</code> - Botun ağzından Discord #çet kanalına mesaj atar.\n"
            f"• <code>/say #oyun mesajınız</code> - Belirttiğin kanala mesaj atar.\n"
            f"• <code>/tts söylenecek laf</code> - Bot ses kanalına girer, tok erkek sesiyle söyler ve çıkar.\n"
            f"• <code>/channels</code> - Sunucudaki kanalları ve seste kimlerin olduğunu gösterir.\n\n"
            f"<b>💾 Yedekleme & Sistem:</b>\n"
            f"• <code>/backup</code> - Anlık canlı hafıza snapshot'ı alıp buraya atar.\n"
            f"• <code>/restart</code> - Discord botunu yeniden başlatır.\n"
            f"• <code>/status</code> - Servislerin ve son yedeğin durumunu gösterir.\n"
            f"• <code>/ping</code> - Botun aktifliğini test eder."
        )
        await send_message(client, token, authorized_chat_id, help_msg)

    elif cmd == "/ping":
        await send_message(client, token, authorized_chat_id, "🏓 Pong! Backup & Kukla botu VDS'te çalışıyor.")

    elif cmd == "/status":
        status_msg = get_service_status()
        await send_message(client, token, authorized_chat_id, status_msg)

    elif cmd in ("/channels", "/kanallar"):
        res = await call_ipc({"action": "channels"})
        if res.get("ok"):
            text_lines = [f"• #{c['name']}" for c in res.get("text_channels", [])]
            voice_lines = []
            for vc in res.get("voice_channels", []):
                members = ", ".join(vc.get("members", [])) or "Boş"
                voice_lines.append(f"• 🔊 <b>#{vc['name']}</b> ({members})")

            out = (
                f"📋 <b>Discord Sunucu Kanalları ({res.get('guild')})</b>\n\n"
                f"<b>Ses Kanalları (ve İçindekiler):</b>\n" + "\n".join(voice_lines) + "\n\n"
                f"<b>Metin Kanalları:</b>\n" + "\n".join(text_lines[:15])
            )
            await send_message(client, token, authorized_chat_id, out)
        else:
            await send_message(client, token, authorized_chat_id, f"❌ Kanal listesi alınamadı:\n<code>{res.get('error')}</code>")

    elif cmd == "/say":
        payload_str = rest
        if not payload_str:
            await send_message(client, token, authorized_chat_id, "Kullanım: <code>/say mesajınız</code> veya <code>/say #kanal mesajınız</code>")
            return

        channel = "çet"
        msg_text = payload_str
        parts = payload_str.split(None, 1)
        if len(parts) >= 2 and parts[0].startswith("#"):
            channel = parts[0]
            msg_text = parts[1]

        res = await call_ipc({"action": "say", "channel": channel, "text": msg_text})
        if res.get("ok"):
            await send_message(client, token, authorized_chat_id, f"✅ <b>Mesaj Discord'a iletildi</b> (#{res.get('channel')}):\n<i>\"{msg_text}\"</i>")
        else:
            await send_message(client, token, authorized_chat_id, f"❌ İletilemedi:\n<code>{res.get('error')}</code>")

    elif cmd == "/tts":
        payload_str = rest
        if not payload_str:
            await send_message(client, token, authorized_chat_id, "Kullanım: <code>/tts söylenecek metin</code> veya <code>/tts #ses-kanalı söylenecek metin</code>")
            return

        channel = ""
        speech_text = payload_str
        parts = payload_str.split(None, 1)
        if len(parts) >= 2 and parts[0].startswith("#"):
            channel = parts[0]
            speech_text = parts[1]

        await send_message(client, token, authorized_chat_id, f"🔊 <b>Sese giriliyor...</b>\n<i>\"{speech_text}\"</i>")
        res = await call_ipc({"action": "tts", "channel": channel, "text": speech_text})
        if res.get("ok"):
            await send_message(client, token, authorized_chat_id, f"✅ <b>Sesli söylendi!</b> (#{res.get('channel')}):\n<i>\"{speech_text}\"</i>")
        else:
            await send_message(client, token, authorized_chat_id, f"❌ Sesli konuşma başarısız:\n<code>{res.get('error')}</code>")

    elif cmd == "/restart":
        target = rest.lower()
        if not target or "discord" in target:
            await send_message(client, token, authorized_chat_id, "🔄 <b>Discord AI Bot servisi yeniden başlatılıyor...</b>")
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: subprocess.run(["systemctl", "restart", "discord-ai-bot.service"], check=True))
                await asyncio.sleep(2)
                status_text = get_service_status()
                await send_message(client, token, authorized_chat_id, f"✅ <b>Yeniden başlatıldı!</b>\n\n{status_text}")
            except Exception as e:
                await send_message(client, token, authorized_chat_id, f"❌ Yeniden başlatma başarısız:\n<code>{e}</code>")
        else:
            await send_message(client, token, authorized_chat_id, f"Bilinmeyen servis: <code>{target}</code>. Kullanım: <code>/restart discord</code>")

    elif cmd == "/backup":
        target = rest.lower()
        if not target or "discord" in target:
            await send_message(
                client,
                token,
                authorized_chat_id,
                "⏳ <b>Discord AI Bot hafıza yedeği hazırlanıyor...</b>\nAtomik SQLite snapshot alınıyor, lütfen birkaç saniye bekle.",
            )
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: execute_backup(trigger_name="Manuel Komut (/backup discord)"))
            except Exception as e:
                logger.error("Manuel backup error: %s", e)
                await send_message(client, token, authorized_chat_id, f"❌ Yedekleme sırasında hata oluştu:\n<code>{e}</code>")
        else:
            await send_message(client, token, authorized_chat_id, f"Bilinmeyen yedek hedefi: <code>{target}</code>. Kullanım: <code>/backup discord</code>")

    else:
        await send_message(client, token, authorized_chat_id, "Bilinmeyen komut. Komut listesi için: /help")


async def run_bot() -> None:
    token, authorized_chat_id = load_env()
    logger.info("Starting Telegram Backup & Puppet Bot daemon for authorized chat_id=%s...", authorized_chat_id)

    offset = 0
    poll_url = f"https://api.telegram.org/bot{token}/getUpdates"

    async with httpx.AsyncClient(timeout=45.0) as client:
        await register_bot_commands(client, token)
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
