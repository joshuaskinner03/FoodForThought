from __future__ import annotations

import math
import secrets
import threading
import time
from typing import Callable

from . import audit
from .config import DEFAULT_AUTO_LOCK_MINUTES, Config
from .db import Database
from .files import AttachmentStore
from .vault import Keys, Vault, WrongPassword

FREE_ATTEMPTS = 5
MAX_BLOCK_SECONDS = 300


class Throttled(Exception):
    def __init__(self, seconds: int):
        super().__init__(f"Too many attempts; wait {seconds}s")
        self.seconds = seconds


class Core:
    def __init__(self, cfg: Config, clock: Callable[[], float] = time.monotonic):
        cfg.ensure_dirs()
        self.cfg = cfg
        self.clock = clock
        self.vault = Vault(cfg.vault_path, cfg.kdf)
        self.mutex = threading.RLock()
        self.db: Database | None = None
        self.keys: Keys | None = None
        self.attachments: AttachmentStore | None = None
        self.token: str | None = None
        self.last_seen = 0.0
        self.auto_lock_minutes = DEFAULT_AUTO_LOCK_MINUTES
        self.failures = 0
        self.blocked_until = 0.0

    @property
    def initialised(self) -> bool:
        return self.vault.exists()

    @property
    def unlocked(self) -> bool:
        return self.db is not None

    def _open(self, keys: Keys) -> str:
        db = Database(self.cfg.db_path, keys.db)
        db.migrate()
        self.db, self.keys = db, keys
        self.attachments = AttachmentStore(self.cfg.attachments_dir, keys.attachments)
        self.auto_lock_minutes = int(db.get_setting("auto_lock_minutes", str(DEFAULT_AUTO_LOCK_MINUTES)))
        audit.flush_pending(self.cfg.pending_audit_path, db)
        self.token = secrets.token_urlsafe(32)
        self.last_seen = self.clock()
        return self.token

    def setup(self, password: str) -> str:
        with self.mutex:
            keys = self.vault.create(password)
            token = self._open(keys)
            audit.record(self.db, "setup")
            return token

    def unlock(self, password: str) -> str:
        with self.mutex:
            now = self.clock()
            if now < self.blocked_until:
                raise Throttled(math.ceil(self.blocked_until - now))
            try:
                keys = self.vault.unlock(password)
            except WrongPassword:
                self.failures += 1
                audit.record_pending(self.cfg.pending_audit_path, "unlock_failed")
                if self.failures >= FREE_ATTEMPTS:
                    wait = min(5 * 2 ** (self.failures - FREE_ATTEMPTS), MAX_BLOCK_SECONDS)
                    self.blocked_until = now + wait
                raise
            self.failures = 0
            self.lock("new sign-in")
            token = self._open(keys)
            audit.record(self.db, "unlock")
            return token

    def lock(self, reason: str = "manual") -> None:
        with self.mutex:
            if self.db is None:
                return
            try:
                audit.record(self.db, "lock", reason)
            finally:
                self.db.close()
                self.db = self.keys = self.attachments = self.token = None

    def valid(self, token: str | None) -> bool:
        current = self.token
        return bool(current and token and secrets.compare_digest(current, token))

    def touch(self) -> None:
        self.last_seen = self.clock()

    def seconds_left(self) -> int:
        if not self.unlocked:
            return 0
        return max(0, int(self.auto_lock_minutes * 60 - (self.clock() - self.last_seen)))

    def expire_if_idle(self) -> bool:
        with self.mutex:
            if self.unlocked and self.clock() - self.last_seen > self.auto_lock_minutes * 60:
                self.lock("auto")
                return True
            return False

    def set_auto_lock(self, minutes: int) -> None:
        with self.mutex:
            self.db.set_setting("auto_lock_minutes", str(minutes))
            self.auto_lock_minutes = minutes
            audit.record(self.db, "settings_changed", f"auto-lock {minutes} min")

    def change_password(self, old: str, new: str) -> None:
        with self.mutex:
            self.vault.change_password(old, new)
            audit.record(self.db, "password_changed")
