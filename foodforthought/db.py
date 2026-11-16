from __future__ import annotations

import threading
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from sqlcipher3 import dbapi2 as sqlcipher

DatabaseError = sqlcipher.DatabaseError


class SchemaTooNew(Exception):
    pass


def _migrations() -> list[tuple[int, str]]:
    found = []
    for entry in resources.files("foodforthought").joinpath("migrations").iterdir():
        if entry.name.endswith(".sql"):
            found.append((int(entry.name.split("_", 1)[0]), entry.read_text("utf-8")))
    return sorted(found)


class Database:
    def __init__(self, path: Path, key: bytes):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.conn = sqlcipher.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlcipher.Row
        try:
            self.conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
            self.conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA secure_delete = ON")
        except Exception:
            self.conn.close()
            raise

    def version(self) -> int:
        return self.query("PRAGMA user_version")[0][0]

    def check_version(self) -> None:
        if self.version() > _migrations()[-1][0]:
            raise SchemaTooNew()

    def migrate(self) -> None:
        with self.lock:
            self.check_version()
            current = self.version()
            for version, sql in _migrations():
                if version <= current:
                    continue
                self.conn.execute("BEGIN")
                try:
                    for statement in filter(None, (s.strip() for s in sql.split(";"))):
                        self.conn.execute(statement)
                    self.conn.execute(f"PRAGMA user_version = {version}")
                    self.conn.execute("COMMIT")
                except Exception:
                    self.conn.execute("ROLLBACK")
                    raise

    @contextmanager
    def transaction(self):
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                yield self
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple = ()):
        with self.lock:
            return self.conn.execute(sql, params)

    def insert(self, sql: str, params: tuple = ()) -> int:
        with self.lock:
            return self.conn.execute(sql, params).lastrowid

    def query(self, sql: str, params: tuple = ()) -> list:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def snapshot(self, dest: Path) -> None:
        with self.lock:
            self.conn.execute("VACUUM INTO ?", (str(dest),))

    def close(self) -> None:
        with self.lock:
            self.conn.close()
