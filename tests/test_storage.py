import json

import pytest

from foodforthought.db import Database, DatabaseError
from foodforthought.files import AttachmentError, AttachmentStore
from foodforthought.vault import Vault, VaultError, WrongPassword

from .conftest import FAST_KDF, PASSWORD


def test_vault_roundtrip(tmp_path):
    vault = Vault(tmp_path / "vault.json", FAST_KDF)
    created = vault.create(PASSWORD)
    assert vault.unlock(PASSWORD).master == created.master
    assert len(created.master) == 32


def test_vault_rejects_wrong_password(tmp_path):
    vault = Vault(tmp_path / "vault.json", FAST_KDF)
    vault.create(PASSWORD)
    with pytest.raises(WrongPassword):
        vault.unlock(PASSWORD + "x")


def test_vault_file_holds_no_plain_key(tmp_path):
    vault = Vault(tmp_path / "vault.json", FAST_KDF)
    keys = vault.create(PASSWORD)
    raw = (tmp_path / "vault.json").read_bytes()
    assert keys.master not in raw
    assert keys.master.hex().encode() not in raw
    assert PASSWORD.encode() not in raw


def test_change_password_keeps_master_key(tmp_path):
    vault = Vault(tmp_path / "vault.json", FAST_KDF)
    keys = vault.create(PASSWORD)
    vault.change_password(PASSWORD, "a brand new password")
    assert vault.unlock("a brand new password").master == keys.master
    with pytest.raises(WrongPassword):
        vault.unlock(PASSWORD)


def test_vault_rejects_absurd_kdf_settings(tmp_path):
    path = tmp_path / "vault.json"
    Vault(path, FAST_KDF).create(PASSWORD)
    doc = json.loads(path.read_text())
    doc["kdf"]["memory_cost"] = 1 << 30
    path.write_text(json.dumps(doc))
    with pytest.raises(VaultError):
        Vault(path).unlock(PASSWORD)


def test_subkeys_differ(tmp_path):
    keys = Vault(tmp_path / "vault.json", FAST_KDF).create(PASSWORD)
    assert len({keys.master, keys.db, keys.attachments, keys.backup_mac}) == 4


def test_database_file_is_encrypted(tmp_path):
    path = tmp_path / "t.db"
    db = Database(path, b"k" * 32)
    db.migrate()
    db.set_setting("marker", "very-recognisable-plaintext")
    db.close()
    raw = path.read_bytes()
    assert not raw.startswith(b"SQLite format 3")
    assert b"very-recognisable-plaintext" not in raw
    assert b"audit_log" not in raw


def test_database_rejects_wrong_key(tmp_path):
    path = tmp_path / "t.db"
    Database(path, b"k" * 32).close()
    db = Database(path, b"k" * 32)
    db.migrate()
    db.close()
    with pytest.raises(DatabaseError):
        Database(path, b"x" * 32)


def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "t.db"
    db = Database(path, b"k" * 32)
    db.migrate()
    version = db.query("PRAGMA user_version")[0][0]
    db.migrate()
    assert db.query("PRAGMA user_version")[0][0] == version >= 1
    db.close()


def test_snapshot_opens_with_same_key(tmp_path):
    db = Database(tmp_path / "t.db", b"k" * 32)
    db.migrate()
    db.set_setting("a", "b")
    db.snapshot(tmp_path / "snap.db")
    db.close()
    snap = Database(tmp_path / "snap.db", b"k" * 32)
    assert snap.get_setting("a") == "b"
    snap.close()
    assert not (tmp_path / "snap.db").read_bytes().startswith(b"SQLite format 3")


def test_attachment_roundtrip_and_encryption(tmp_path):
    store = AttachmentStore(tmp_path, b"a" * 32)
    file_id = store.put(b"scan of a paper food diary")
    assert store.get(file_id) == b"scan of a paper food diary"
    assert b"paper food diary" not in (tmp_path / f"{file_id}.bin").read_bytes()
    store.delete(file_id)
    with pytest.raises(AttachmentError):
        store.get(file_id)


def test_attachment_detects_tampering_and_swaps(tmp_path):
    store = AttachmentStore(tmp_path, b"a" * 32)
    first, second = store.put(b"one"), store.put(b"two")
    (tmp_path / f"{first}.bin").write_bytes((tmp_path / f"{second}.bin").read_bytes())
    with pytest.raises(AttachmentError):
        store.get(first)
    with pytest.raises(AttachmentError):
        store.get("../vault")
