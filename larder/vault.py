from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import KdfParams

VAULT_VERSION = 1


class WrongPassword(Exception):
    pass


class VaultError(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _derive_kek(password: str, salt: bytes, params: KdfParams) -> bytes:
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=params.time_cost,
        memory_cost=params.memory_cost,
        parallelism=params.parallelism,
        hash_len=32,
        type=Type.ID,
    )


def _subkey(master: bytes, info: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info.encode()).derive(master)


@dataclass(frozen=True)
class Keys:
    master: bytes

    @property
    def db(self) -> bytes:
        return _subkey(self.master, "larder/db")

    @property
    def attachments(self) -> bytes:
        return _subkey(self.master, "larder/attachments")

    @property
    def backup_mac(self) -> bytes:
        return _subkey(self.master, "larder/backup-mac")


def _wrap(password: str, master: bytes, params: KdfParams) -> dict:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    kek = _derive_kek(password, salt, params)
    ct = AESGCM(kek).encrypt(nonce, master, b"larder/vault")
    return {
        "version": VAULT_VERSION,
        "kdf": {
            "name": "argon2id",
            "salt": _b64(salt),
            "time_cost": params.time_cost,
            "memory_cost": params.memory_cost,
            "parallelism": params.parallelism,
        },
        "wrapped_key": {"nonce": _b64(nonce), "ciphertext": _b64(ct)},
    }


def _unwrap(password: str, doc: dict) -> bytes:
    try:
        if doc["version"] != VAULT_VERSION or doc["kdf"]["name"] != "argon2id":
            raise VaultError("Unsupported vault format")
        kdf = doc["kdf"]
        params = KdfParams(int(kdf["time_cost"]), int(kdf["memory_cost"]), int(kdf["parallelism"]))
        if not (1 <= params.time_cost <= 20 and 8 <= params.memory_cost <= 1 << 20 and 1 <= params.parallelism <= 16):
            raise VaultError("Unsupported vault settings")
        salt = _unb64(kdf["salt"])
        nonce = _unb64(doc["wrapped_key"]["nonce"])
        ct = _unb64(doc["wrapped_key"]["ciphertext"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VaultError("Vault file is damaged") from exc
    kek = _derive_kek(password, salt, params)
    try:
        return AESGCM(kek).decrypt(nonce, ct, b"larder/vault")
    except InvalidTag as exc:
        raise WrongPassword() from exc


class Vault:
    def __init__(self, path: Path, params: KdfParams | None = None):
        self.path = Path(path)
        self.params = params or KdfParams()

    def exists(self) -> bool:
        return self.path.exists()

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise VaultError("Vault file is missing or damaged") from exc

    def _write(self, doc: dict) -> None:
        tmp = self.path.with_suffix(".json.new")
        tmp.write_text(json.dumps(doc, indent=2), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    def create(self, password: str) -> Keys:
        if self.exists():
            raise VaultError("Vault already exists")
        master = os.urandom(32)
        self._write(_wrap(password, master, self.params))
        return Keys(master)

    def unlock(self, password: str) -> Keys:
        return Keys(_unwrap(password, self._read()))

    def change_password(self, old: str, new: str) -> None:
        master = _unwrap(old, self._read())
        self._write(_wrap(new, master, self.params))
