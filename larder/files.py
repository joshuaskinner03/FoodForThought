from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class AttachmentError(Exception):
    pass


class AttachmentStore:
    def __init__(self, root: Path, key: bytes):
        self.root = Path(root)
        self.aead = AESGCM(key)

    def _path(self, file_id: str) -> Path:
        if not file_id.isalnum():
            raise AttachmentError("Invalid attachment id")
        return self.root / f"{file_id}.bin"

    def put(self, data: bytes) -> str:
        file_id = secrets.token_hex(16)
        nonce = os.urandom(12)
        blob = nonce + self.aead.encrypt(nonce, data, file_id.encode())
        path = self._path(file_id)
        tmp = path.with_suffix(".new")
        tmp.write_bytes(blob)
        os.replace(tmp, path)
        return file_id

    def get(self, file_id: str) -> bytes:
        try:
            blob = self._path(file_id).read_bytes()
        except OSError as exc:
            raise AttachmentError("Attachment not found") from exc
        try:
            return self.aead.decrypt(blob[:12], blob[12:], file_id.encode())
        except InvalidTag as exc:
            raise AttachmentError("Attachment is damaged") from exc

    def delete(self, file_id: str) -> None:
        self._path(file_id).unlink(missing_ok=True)
