"""Read-only recovery rehearsal: unpack into a disposable directory, check DBs.

Usage: .venv/bin/python scripts/check_restore.py /path/to/backup.tar.gz
Never modifies the live memory directory or sends messages.
"""
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile


def check_restore(path, keyfile=None):
    with tempfile.TemporaryDirectory(prefix="discord-restore-check-") as folder:
        if str(path).endswith(".enc"):
            if not keyfile:
                raise ValueError("Recovery key file is required")
            from backup_crypto import decrypt_backup
            decrypted=Path(folder)/"snapshot.tar.gz"
            decrypted.write_bytes(decrypt_backup(path,keyfile))
            path=decrypted
        with tarfile.open(path, "r:gz") as archive:
            archive.extractall(folder, filter="data")
        results = {}
        databases = list(Path(folder).rglob("*.sqlite3")) + list(Path(folder).rglob("*.db"))
        for database in databases:
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
                result = db.execute("PRAGMA quick_check").fetchone()[0]
                if result != "ok":
                    raise RuntimeError(f"{database.name}: {result}")
                results[database.name] = result
        if "archive.sqlite3" not in results:
            raise RuntimeError("Missing archive database")
        return results


if __name__ == "__main__":
    print(json.dumps(check_restore(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else None)))
