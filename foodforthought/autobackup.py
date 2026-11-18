from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import audit, backup
from .config import Config

PATTERN = re.compile(r"^foodforthought-backup-\d{8}-\d{6}\.fftbackup$")
EVERY = timedelta(hours=24)
CHECK_SECONDS = 300
KEEP_CHOICES = (7, 14, 30)
SHARED = Path("/mnt/chromeos")


class BackupFolderProblem(Exception):
    pass


def suggested_folders(root: Path = SHARED) -> list[str]:
    found = []
    if root.is_dir():
        for top in sorted(root.iterdir()):
            if top.is_dir():
                found.append(str(top))
                found += [str(child) for child in sorted(top.iterdir()) if child.is_dir() and not child.name.startswith(".")][:12]
    return found


def check_folder(cfg: Config, raw: str) -> Path:
    if not raw.strip():
        raise BackupFolderProblem("Enter a folder.")
    folder = Path(raw.strip()).expanduser()
    if not folder.is_absolute():
        raise BackupFolderProblem("Enter the full path of the folder, starting with /.")
    if not folder.is_dir():
        raise BackupFolderProblem("That folder does not exist. On a Chromebook, share it with Linux first.")
    resolved = folder.resolve()
    if resolved == cfg.data_dir.resolve() or cfg.data_dir.resolve() in resolved.parents:
        raise BackupFolderProblem("Choose a folder outside the data folder, so a backup survives if the data folder is lost.")
    if not os.access(resolved, os.W_OK):
        raise BackupFolderProblem("FoodForThought cannot write to that folder.")
    return resolved


def settings(db) -> dict:
    keep = db.get_setting("auto_backup_keep", "14")
    return {
        "folder": db.get_setting("auto_backup_dir", ""),
        "keep": int(keep) if keep.isdigit() else 14,
        "last": db.get_setting("auto_backup_last", ""),
        "error": db.get_setting("auto_backup_error", ""),
    }


def due(db, now: datetime | None = None) -> bool:
    found = settings(db)
    if not found["folder"]:
        return False
    if not found["last"]:
        return True
    try:
        last = datetime.fromisoformat(found["last"].replace("Z", "+00:00"))
    except ValueError:
        return True
    return (now or datetime.now(timezone.utc)) - last >= EVERY


def prune(folder: Path, keep: int) -> list[str]:
    ours = sorted((p for p in folder.iterdir() if p.is_file() and PATTERN.match(p.name)), key=lambda p: p.name)
    removed = []
    for path in ours[: max(0, len(ours) - keep)]:
        path.unlink(missing_ok=True)
        removed.append(path.name)
    return removed


def run(cfg: Config, core) -> str:
    db, keys = core.db, core.keys
    if db is None or keys is None:
        raise BackupFolderProblem("FoodForThought is locked.")
    found = settings(db)
    try:
        folder = check_folder(cfg, found["folder"])
        name = backup.backup_filename()
        partial = folder / f".{name}.part"
        try:
            backup.create_backup(cfg, db, keys, partial)
            os.replace(partial, folder / name)
        finally:
            partial.unlink(missing_ok=True)
        prune(folder, found["keep"])
    except Exception as exc:
        message = str(exc) if isinstance(exc, BackupFolderProblem) else f"The backup could not be written: {exc}"
        try:
            db.set_setting("auto_backup_error", message)
            audit.record(db, "auto_backup_failed", message[:200])
        except Exception:
            pass
        raise BackupFolderProblem(message) from exc
    stamp = audit.now_iso()
    db.set_setting("auto_backup_last", stamp)
    db.set_setting("last_backup", stamp)
    db.set_setting("auto_backup_error", "")
    audit.record(db, "auto_backup_created", name)
    return name


class Scheduler:
    def __init__(self, cfg: Config, core):
        self.cfg, self.core = cfg, core
        self.busy = threading.Lock()

    def run_if_due(self) -> None:
        if not self.busy.acquire(blocking=False):
            return
        try:
            db = self.core.db
            if db is not None and due(db):
                run(self.cfg, self.core)
        except Exception:
            pass
        finally:
            self.busy.release()

    def soon(self) -> None:
        threading.Thread(target=self.run_if_due, daemon=True).start()
