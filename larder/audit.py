from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .db import Database


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(db: Database, action: str, detail: str = "", ts: str | None = None) -> None:
    db.execute(
        "INSERT INTO audit_log(ts, action, detail) VALUES(?, ?, ?)",
        (ts or now_iso(), action, detail),
    )


def record_pending(path: Path, action: str, detail: str = "") -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": now_iso(), "action": action, "detail": detail}) + "\n")


def flush_pending(path: Path, db: Database) -> None:
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        try:
            item = json.loads(line)
            record(db, str(item["action"]), str(item.get("detail", "")), str(item["ts"]))
        except (ValueError, KeyError):
            continue
    path.unlink()


def recent(db: Database, limit: int = 200) -> list:
    return db.query("SELECT ts, action, detail FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
