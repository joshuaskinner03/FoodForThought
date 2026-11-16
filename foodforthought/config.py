from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_data_dir

DEFAULT_PORT = 8765
MIN_PASSWORD_LENGTH = 12
DEFAULT_AUTO_LOCK_MINUTES = 10
APP_DIR = "foodforthought"
LEGACY_APP_DIR = "larder"
LEGACY_DB_NAME = "larder.db"


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
        return self.data_dir / "records.db"

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
        legacy_db = self.data_dir / LEGACY_DB_NAME
        if legacy_db.exists() and not self.db_path.exists():
            legacy_db.rename(self.db_path)


def adopt_legacy_dir(old: Path, new: Path) -> bool:
    if not (old / "vault.json").exists() or (new / "vault.json").exists():
        return False
    if new.exists():
        if any(p.is_file() for p in new.rglob("*")):
            return False
        shutil.rmtree(new)
    new.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old), str(new))
    return True


def default_data_dir() -> Path:
    override = os.environ.get("FOODFORTHOUGHT_DATA")
    if override:
        return Path(override).expanduser()
    new = Path(user_data_dir(APP_DIR, appauthor=False))
    adopt_legacy_dir(Path(user_data_dir(LEGACY_APP_DIR, appauthor=False)), new)
    return new
