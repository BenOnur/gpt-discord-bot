#!/usr/bin/env python3
"""
Discord AI Bot Memory Backup to Telegram DM.
Creates an atomic, consistent snapshot of the bot's memory:
- archive.sqlite3 (using SQLite Online Backup API)
- reminders.sqlite3 (using SQLite Online Backup API)
- config/memory.db (using SQLite Online Backup API)
- config/config.json
- vault/ (markdown knowledge notes)

Excludes:
- fastembed_cache (downloadable model weights)
- cache/ (transient runtime cache)

Compresses the snapshot with gzip (~13 MB) and sends it directly to Onur's
private Telegram chat via the Telegram Bot API.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("telegram_backup")

MEMORY_DIR = Path(os.environ.get("DISCORD_MEMORY_DIR", "/var/lib/discord-ai-memory"))
BACKUP_STORE_DIR = Path(os.environ.get("BACKUP_STORE_DIR", "/var/backups/discord-memory"))
ENV_FILE = Path("/etc/discord-ai-bot/backup.env")


def load_env() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        if ENV_FILE.exists():
            for line in ENV_FILE.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("\"'")
                    if k == "TELEGRAM_TOKEN" and not token:
                        token = v
                    elif k == "TELEGRAM_CHAT_ID" and not chat_id:
                        chat_id = v

    if not token or not chat_id:
        raise ValueError("TELEGRAM_TOKEN and TELEGRAM_CHAT_ID must be set in env or /etc/discord-ai-bot/backup.env")

    return token, chat_id


def backup_sqlite(src_path: Path, dst_path: Path) -> None:
    """Atomic, non-locking online SQLite backup."""
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if not src_path.exists():
        logger.warning("SQLite source %s does not exist, skipping", src_path)
        return

    logger.info("Taking live atomic snapshot of %s -> %s", src_path.name, dst_path)
    with sqlite3.connect(src_path) as src:
        with sqlite3.connect(dst_path) as dst:
            src.backup(dst)


def create_snapshot(staging_dir: Path) -> None:
    """Copy all essential memory components to staging directory."""
    staging_dir.mkdir(parents=True, exist_ok=True)

    # 1. Atomic backup of SQLite databases
    backup_sqlite(MEMORY_DIR / "archive.sqlite3", staging_dir / "archive.sqlite3")
    backup_sqlite(MEMORY_DIR / "reminders.sqlite3", staging_dir / "reminders.sqlite3")

    # 2. Config directory (memory.db + config.json, excluding model cache)
    config_src = MEMORY_DIR / "config"
    config_dst = staging_dir / "config"
    config_dst.mkdir(parents=True, exist_ok=True)

    if (config_src / "memory.db").exists():
        backup_sqlite(config_src / "memory.db", config_dst / "memory.db")

    if (config_src / "config.json").exists():
        shutil.copy2(config_src / "config.json", config_dst / "config.json")

    # 3. Vault (markdown notes and profiles)
    vault_src = MEMORY_DIR / "vault"
    vault_dst = staging_dir / "vault"
    if vault_src.exists():
        logger.info("Copying knowledge vault...")
        shutil.copytree(vault_src, vault_dst, dirs_exist_ok=True)


def rotate_local_backups(keep_count: int = 3) -> None:
    """Keep only the latest N backup files locally to conserve disk space."""
    if not BACKUP_STORE_DIR.exists():
        return
    backups = sorted(BACKUP_STORE_DIR.glob("discord-memory-*.tar.gz"), key=lambda p: p.stat().st_mtime)
    while len(backups) > keep_count:
        oldest = backups.pop(0)
        try:
            oldest.unlink()
            logger.info("Removed old local backup: %s", oldest.name)
        except OSError as e:
            logger.warning("Failed to remove old backup %s: %s", oldest, e)


def send_to_telegram(tar_path: Path, token: str, chat_id: str, trigger_name: str = "Otomatik Zamanlayıcı") -> None:
    """Send backup archive via Telegram Bot API sendDocument."""
    size_mb = tar_path.stat().st_size / (1024 * 1024)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    caption = (
        f"🧠 <b>Discord AI Bot Hafıza Yedeği</b>\n"
        f"📅 Tarih: <code>{now_str}</code>\n"
        f"📦 Boyut: <b>{size_mb:.2f} MB</b>\n"
        f"💾 İçerik: <code>archive.sqlite3 + vault + memory.db</code>\n"
        f"⚡ <b>Tetikleme:</b> {trigger_name}\n"
        f"🔒 <b>Durum:</b> Atomik Canlı Snapshot Alındı"
    )

    url = f"https://api.telegram.org/bot{token}/sendDocument"
    logger.info("Uploading %s (%.2f MB) to Telegram chat %s...", tar_path.name, size_mb, chat_id)

    with open(tar_path, "rb") as f:
        files = {
            "document": (tar_path.name, f, "application/gzip"),
        }
        data = {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML",
        }
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(url, data=data, files=files)
            if resp.status_code != 200:
                logger.error("Telegram API error (%d): %s", resp.status_code, resp.text)
                resp.raise_for_status()

    logger.info("Backup successfully delivered to Telegram!")


def execute_backup(trigger_name: str = "Otomatik Zamanlayıcı") -> Path:
    """Execute complete atomic backup workflow and return path to created archive."""
    token, chat_id = load_env()
    BACKUP_STORE_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    archive_name = f"discord-memory-{timestamp}.tar.gz"
    tar_path = BACKUP_STORE_DIR / archive_name

    with tempfile.TemporaryDirectory(prefix="discord_mem_snap_") as tmp_dir:
        staging = Path(tmp_dir) / "data"
        logger.info("Creating memory snapshot in %s...", staging)
        create_snapshot(staging)

        # Create tar.gz archive
        logger.info("Compressing snapshot to %s...", tar_path)
        shutil.make_archive(
            base_name=str(tar_path).removesuffix(".tar.gz"),
            format="gztar",
            root_dir=staging,
        )

    size_mb = tar_path.stat().st_size / (1024 * 1024)
    logger.info("Created compressed archive %s (%.2f MB)", tar_path.name, size_mb)

    # Send to Telegram DM
    send_to_telegram(tar_path, token, chat_id, trigger_name=trigger_name)

    # Rotate local backups
    rotate_local_backups(keep_count=3)

    return tar_path


def main() -> int:
    execute_backup(trigger_name="Otomatik Zamanlayıcı (04:00)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
