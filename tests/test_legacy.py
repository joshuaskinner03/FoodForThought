import io
import json
import tarfile

from foodforthought import backup
from foodforthought.config import Config, adopt_legacy_dir
from foodforthought.db import Database
from foodforthought.vault import Vault

from .conftest import FAST_KDF, PASSWORD, core_of
from .test_backup import restore


def test_old_data_folder_is_adopted(tmp_path):
    old, new = tmp_path / "larder", tmp_path / "foodforthought"
    old.mkdir()
    (old / "vault.json").write_text("{}")
    (old / "larder.db").write_bytes(b"x")
    (new / "tmp").mkdir(parents=True)
    assert adopt_legacy_dir(old, new)
    assert not old.exists() and (new / "vault.json").exists()
    Config(data_dir=new).ensure_dirs()
    assert (new / "records.db").read_bytes() == b"x" and not (new / "larder.db").exists()


def test_existing_new_data_is_never_overwritten(tmp_path):
    old, new = tmp_path / "larder", tmp_path / "foodforthought"
    for folder, marker in ((old, "old"), (new, "new")):
        folder.mkdir()
        (folder / "vault.json").write_text(marker)
    assert not adopt_legacy_dir(old, new)
    assert (new / "vault.json").read_text() == "new" and old.exists()


def test_phase0_database_upgrades_and_keeps_its_contents(tmp_path, client, cfg):
    cfg.ensure_dirs()
    keys = Vault(cfg.vault_path, FAST_KDF).create(PASSWORD)
    db = Database(cfg.data_dir / "larder.db", keys.db)
    db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')"
    )
    db.execute("INSERT INTO settings VALUES('auto_lock_minutes', '7')")
    db.execute("PRAGMA user_version = 1")
    db.close()
    cfg.ensure_dirs()
    assert client.post("/unlock", data={"password": PASSWORD}).status_code == 303
    core = core_of(client)
    assert core.auto_lock_minutes == 7
    assert core.db.version() >= 2
    assert client.get("/clients").status_code == 200
    assert core.db.one("SELECT count(*) FROM note_template")[0] == 2


def test_phase0_backup_still_restores(unlocked, cfg, tmp_path):
    core = core_of(unlocked)
    core.db.set_setting("marker", "from the old app")
    snapshot = tmp_path / "larder.db"
    core.db.snapshot(snapshot)
    members = {"vault.json": cfg.vault_path.read_bytes(), "larder.db": snapshot.read_bytes()}
    body = {
        "format": 1,
        "created": "2026-11-16T19:00:00Z",
        "app_version": "0.0.1",
        "files": {name: backup._sha256(path) for name, path in (("vault.json", cfg.vault_path), ("larder.db", snapshot))},
    }
    members["manifest.json"] = json.dumps(dict(body, mac=backup._mac(core.keys.backup_mac, body))).encode()
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    core.db.set_setting("marker", "changed")
    assert restore(unlocked, out.getvalue()).status_code == 303
    unlocked.post("/unlock", data={"password": PASSWORD})
    assert core_of(unlocked).db.get_setting("marker") == "from the old app"
    assert cfg.db_path.name == "records.db" and cfg.db_path.exists()


def test_backup_from_a_newer_version_is_refused(unlocked):
    core_of(unlocked).db.execute("PRAGMA user_version = 999")
    data = unlocked.post("/backup").content
    core_of(unlocked).db.execute("PRAGMA user_version = 2")
    response = restore(unlocked, data)
    assert response.status_code == 400 and "newer version" in response.text
    assert core_of(unlocked).unlocked
