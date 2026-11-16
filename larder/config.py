from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_data_dir

DEFAULT_PORT = 8765
MIN_PASSWORD_LENGTH = 12
DEFAULT_AUTO_LOCK_MINUTES = 10


@dataclass(frozen=True)
class KdfParams:
    time_cost: int = 3
    memory_cost: int = 65536
    parallelism: int = 2


@dataclass(frozen=True)
class Config:
    data_dir: Path
    port: int = DEFAULT_PORT
    kdf: KdfParams = field(default_factory=KdfParams)

    @property
    def vault_path(self) -> Path:
        return self.data_dir / "vault.json"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "larder.db"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def pending_audit_path(self) -> Path:
        return self.data_dir / "pending_audit.jsonl"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.attachments_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.data_dir, 0o700)
        except OSError:
            pass


def default_data_dir() -> Path:
    override = os.environ.get("LARDER_DATA")
    if override:
        return Path(override).expanduser()
    return Path(user_data_dir("larder", appauthor=False))
