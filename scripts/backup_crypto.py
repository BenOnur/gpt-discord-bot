"""Authenticated encryption for backup files; recovery key is stored separately."""
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC=b"DISCORDBRAIN1\x00"


def encrypt_backup(path, keyfile):
    path, keyfile=Path(path),Path(keyfile)
    key=keyfile.read_bytes()
    if len(key)!=32:
        raise ValueError("Backup key must contain 32 random bytes")
    nonce=os.urandom(12)
    output=path.with_name(path.name+".enc")
    with output.open("xb") as stream:
        os.chmod(output,0o600)
        stream.write(MAGIC+nonce+AESGCM(key).encrypt(nonce,path.read_bytes(),MAGIC))
    path.unlink()
    return output


def decrypt_backup(path, keyfile):
    data=Path(path).read_bytes()
    if not data.startswith(MAGIC):
        raise ValueError("Invalid encrypted backup header")
    nonce=data[len(MAGIC):len(MAGIC)+12]
    return AESGCM(Path(keyfile).read_bytes()).decrypt(nonce,data[len(MAGIC)+12:],MAGIC)
