from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import shutil
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from . import __version__
from .audit import now_iso
from .config import LEGACY_DB_NAME, Config
from .db import Database, DatabaseError, SchemaTooNew
from .vault import Keys, Vault, VaultError, WrongPassword

FORMAT = 1
BACKUP_SUFFIX = ".fftbackup"
DB_NAME = "records.db"
DB_NAMES = (DB_NAME, LEGACY_DB_NAME)
_ATTACHMENT_NAME = re.compile(r"[0-9a-f]{32}\.bin")
_SIGNED_FIELDS = ("format", "created", "app_version", "files")


class BackupError(Exception):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mac(key: bytes, body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


def _plain(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o600
    return info


def backup_filename() -> str:
    return f"foodforthought-backup-{time.strftime('%Y%m%d-%H%M%S')}{BACKUP_SUFFIX}"


def create_backup(cfg: Config, db: Database, keys: Keys, dest: Path) -> None:
    stage = Path(tempfile.mkdtemp(dir=cfg.tmp_dir))
    try:
        snapshot = stage / DB_NAME
        db.snapshot(snapshot)
        members = {"vault.json": cfg.vault_path, DB_NAME: snapshot}
        for path in sorted(cfg.attachments_dir.glob("*.bin")):
            members[f"attachments/{path.name}"] = path
        body = {
            "format": FORMAT,
            "created": now_iso(),
            "app_version": __version__,
            "files": {name: _sha256(path) for name, path in members.items()},
        }
        manifest = json.dumps(dict(body, mac=_mac(keys.backup_mac, body)), indent=2).encode()
        with tarfile.open(dest, "w") as tar:
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest)
            info.mtime = int(time.time())
            tar.addfile(_plain(info), io.BytesIO(manifest))
            for name, path in members.items():
                tar.add(path, arcname=name, recursive=False, filter=_plain)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _allowed(name: str) -> bool:
    if name in ("manifest.json", "vault.json", *DB_NAMES):
        return True
    parts = PurePosixPath(name).parts
    return len(parts) == 2 and parts[0] == "attachments" and bool(_ATTACHMENT_NAME.fullmatch(parts[1]))


def _unpack(source: Path, stage: Path) -> None:
    try:
        tar = tarfile.open(source, "r:")
    except (tarfile.TarError, OSError) as exc:
        raise BackupError("That file is not a FoodForThought backup.") from exc
    with tar:
        try:
            for member in tar:
                if not member.isfile() or not _allowed(member.name):
                    raise BackupError("That file is not a FoodForThought backup.")
                target = stage / member.name
                target.parent.mkdir(exist_ok=True)
                with tar.extractfile(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
        except tarfile.TarError as exc:
            raise BackupError("The backup file is damaged.") from exc


def unpack_and_verify(cfg: Config, source: Path, password: str) -> Path:
    stage = Path(tempfile.mkdtemp(dir=cfg.tmp_dir))
    try:
        _unpack(source, stage)
        try:
            manifest = json.loads((stage / "manifest.json").read_text("utf-8"))
            body = {field: manifest[field] for field in _SIGNED_FIELDS}
            mac = str(manifest["mac"])
            files = dict(body["files"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise BackupError("That file is not a FoodForThought backup.") from exc
        if body["format"] != FORMAT:
            raise BackupError("This backup was made by a newer version of FoodForThought.")
        try:
            keys = Vault(stage / "vault.json").unlock(password)
        except WrongPassword as exc:
            raise BackupError("Wrong password for this backup.") from exc
        except VaultError as exc:
            raise BackupError("The backup file is damaged.") from exc
        if not hmac.compare_digest(_mac(keys.backup_mac, body), mac):
            raise BackupError("The backup file is damaged or has been altered.")
        on_disk = {
            p.relative_to(stage).as_posix()
            for p in stage.rglob("*")
            if p.is_file() and p.name != "manifest.json"
        }
        db_names = [name for name in DB_NAMES if name in on_disk]
        if on_disk != set(files) or "vault.json" not in on_disk or len(db_names) != 1:
            raise BackupError("The backup file is damaged or has been altered.")
        for name, expected in files.items():
            if not hmac.compare_digest(_sha256(stage / name), str(expected)):
                raise BackupError("The backup file is damaged or has been altered.")
        if db_names[0] != DB_NAME:
            (stage / db_names[0]).rename(stage / DB_NAME)
        try:
            db = Database(stage / DB_NAME, keys.db)
        except DatabaseError as exc:
            raise BackupError("The backup file is damaged.") from exc
        try:
            db.check_version()
        except SchemaTooNew as exc:
            raise BackupError("This backup was made by a newer version of FoodForThought.") from exc
        finally:
            db.close()
        return stage
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def safety_copy(cfg: Config) -> Path | None:
    copies = sorted(cfg.data_dir.glob("pre-restore-*"))
    return copies[-1] if copies else None


def delete_safety_copies(cfg: Config) -> None:
    for old in cfg.data_dir.glob("pre-restore-*"):
        shutil.rmtree(old, ignore_errors=True)


def install_restored(cfg: Config, stage: Path) -> Path | None:
    delete_safety_copies(cfg)
    existing = [p for p in (cfg.vault_path, cfg.db_path, cfg.attachments_dir) if p.exists()]
    keep = None
    if cfg.vault_path.exists():
        keep = cfg.data_dir / f"pre-restore-{time.strftime('%Y%m%d-%H%M%S')}"
        keep.mkdir()
        for path in existing:
            shutil.move(str(path), str(keep / path.name))
    else:
        for path in existing:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    shutil.move(str(stage / "vault.json"), str(cfg.vault_path))
    shutil.move(str(stage / DB_NAME), str(cfg.db_path))
    if (stage / "attachments").exists():
        shutil.move(str(stage / "attachments"), str(cfg.attachments_dir))
    cfg.attachments_dir.mkdir(exist_ok=True)
    shutil.rmtree(stage, ignore_errors=True)
    return keep
