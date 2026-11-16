import io
import json
import tarfile

import pytest
from fastapi.testclient import TestClient

from foodforthought.app import create_app
from foodforthought.config import Config

from .conftest import BASE_URL, FAST_KDF, PASSWORD, core_of


def make_backup(client) -> bytes:
    response = client.post("/backup")
    assert response.status_code == 200
    assert ".fftbackup" in response.headers["content-disposition"]
    return response.content


def restore(client, data: bytes, password: str = PASSWORD):
    return client.post(
        "/restore",
        data={"password": password},
        files={"file": ("backup.fftbackup", data, "application/octet-stream")},
    )


def rebuild(data: bytes, mutate) -> bytes:
    members = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar:
            members[member.name] = tar.extractfile(member).read()
    mutate(members)
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return out.getvalue()


@pytest.fixture
def populated(unlocked):
    core = core_of(unlocked)
    core.db.set_setting("marker", "kept")
    core.file_id = core.attachments.put(b"scanned diary")
    return unlocked


def test_backup_contains_no_plaintext(populated):
    data = make_backup(populated)
    assert b"SQLite format 3" not in data
    assert b"scanned diary" not in data
    assert b"marker" not in data
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        names = set(tar.getnames())
    assert {"manifest.json", "vault.json", "records.db"} <= names
    assert any(name.startswith("attachments/") for name in names)
    assert core_of(populated).db.get_setting("last_backup")


def test_restore_onto_a_fresh_install(populated, tmp_path, clock):
    data = make_backup(populated)
    file_id = core_of(populated).file_id
    fresh_cfg = Config(data_dir=tmp_path / "new-device", kdf=FAST_KDF)
    with TestClient(create_app(fresh_cfg, clock), base_url=BASE_URL, follow_redirects=False) as fresh:
        assert restore(fresh, data).headers["location"] == "/unlock?msg=restored"
        assert fresh.post("/unlock", data={"password": PASSWORD}).status_code == 303
        core = core_of(fresh)
        assert core.db.get_setting("marker") == "kept"
        assert core.attachments.get(file_id) == b"scanned diary"
        actions = [row["action"] for row in core.db.query("SELECT action FROM audit_log")]
        assert "restore" in actions
        assert not list(fresh_cfg.data_dir.glob("pre-restore-*"))


def test_restore_over_existing_data_keeps_safety_copy(populated, cfg):
    data = make_backup(populated)
    core_of(populated).db.set_setting("marker", "changed after backup")
    assert restore(populated, data).status_code == 303
    assert not core_of(populated).unlocked
    populated.post("/unlock", data={"password": PASSWORD})
    assert core_of(populated).db.get_setting("marker") == "kept"
    copies = list(cfg.data_dir.glob("pre-restore-*"))
    assert len(copies) == 1 and (copies[0] / "records.db").exists()
    assert copies[0].name in populated.get("/settings").text
    assert populated.post("/settings/safety-copy/delete").status_code == 303
    assert not list(cfg.data_dir.glob("pre-restore-*"))


def test_restore_uses_the_password_from_backup_time(populated):
    data = make_backup(populated)
    new = "a brand new password"
    populated.post("/settings/password", data={"current": PASSWORD, "password": new, "confirm": new})
    assert restore(populated, data, new).status_code == 400
    assert restore(populated, data, PASSWORD).status_code == 303
    assert populated.post("/unlock", data={"password": new}).status_code == 401
    assert populated.post("/unlock", data={"password": PASSWORD}).status_code == 303


def test_wrong_password_leaves_data_untouched(populated, cfg):
    data = make_backup(populated)
    core_of(populated).db.set_setting("marker", "current")
    response = restore(populated, data, "not the password")
    assert response.status_code == 400 and "Wrong password" in response.text
    assert core_of(populated).unlocked
    assert core_of(populated).db.get_setting("marker") == "current"
    assert not list(cfg.data_dir.glob("pre-restore-*"))
    assert not list(cfg.tmp_dir.iterdir())


def test_rejects_non_backup_files(populated):
    assert restore(populated, b"this is not a backup").status_code == 400
    assert restore(populated, b"").status_code == 400
    assert core_of(populated).db.get_setting("marker") == "kept"


def test_rejects_tampered_database(populated):
    def flip(members):
        blob = bytearray(members["records.db"])
        blob[-1] ^= 1
        members["records.db"] = bytes(blob)

    response = restore(populated, rebuild(make_backup(populated), flip))
    assert response.status_code == 400 and "altered" in response.text


def test_rejects_tampered_manifest(populated):
    def edit(members):
        manifest = json.loads(members["manifest.json"])
        blob = bytearray(members["records.db"])
        blob[-1] ^= 1
        members["records.db"] = bytes(blob)
        import hashlib

        manifest["files"]["records.db"] = hashlib.sha256(members["records.db"]).hexdigest()
        members["manifest.json"] = json.dumps(manifest).encode()

    response = restore(populated, rebuild(make_backup(populated), edit))
    assert response.status_code == 400 and "altered" in response.text


def test_rejects_extra_or_escaping_members(populated, cfg):
    def traversal(members):
        members["../escaped.txt"] = b"x"

    def extra(members):
        members["attachments/" + "a" * 32 + ".bin"] = b"x"

    def missing(members):
        del members["records.db"]

    data = make_backup(populated)
    for mutate in (traversal, extra, missing):
        assert restore(populated, rebuild(data, mutate)).status_code == 400
    assert not (cfg.data_dir.parent / "escaped.txt").exists()
    assert not (cfg.tmp_dir.parent / "escaped.txt").exists()
    assert core_of(populated).db.get_setting("marker") == "kept"


def test_rejects_symlink_members(populated):
    data = make_backup(populated)
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        with tarfile.open(fileobj=io.BytesIO(data)) as src:
            for member in src:
                tar.addfile(member, src.extractfile(member))
        link = tarfile.TarInfo("vault.json")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    assert restore(populated, out.getvalue()).status_code == 400


def test_backup_records_itself(populated, tmp_path, clock):
    data = make_backup(populated)
    fresh_cfg = Config(data_dir=tmp_path / "new-device", kdf=FAST_KDF)
    with TestClient(create_app(fresh_cfg, clock), base_url=BASE_URL, follow_redirects=False) as fresh:
        restore(fresh, data)
        fresh.post("/unlock", data={"password": PASSWORD})
        core = core_of(fresh)
        assert core.db.get_setting("last_backup")
        actions = [row["action"] for row in core.db.query("SELECT action FROM audit_log ORDER BY id")]
        assert actions[-3:] == ["backup_created", "restore", "unlock"]
