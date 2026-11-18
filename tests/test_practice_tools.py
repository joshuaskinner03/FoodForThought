import csv
import io
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from foodforthought import autobackup, backup
from foodforthought.invoices import keep_until, kept_invoices

from .conftest import PASSWORD, core_of
from .test_clients import add_client
from .test_invoices import draft, issue, practice  # noqa: F401


def db(client):
    return core_of(client).db


@pytest.fixture
def numbered(monkeypatch):
    names = iter(f"foodforthought-backup-20261118-1200{n:02d}.fftbackup" for n in range(60))
    monkeypatch.setattr(backup, "backup_filename", lambda: next(names))


def test_automatic_backups(unlocked, cfg, tmp_path, numbered):
    folder = tmp_path / "Drive"
    folder.mkdir()
    page = unlocked.get("/settings").text
    assert "Automatic backups" in page and "Turn on and back up now" in page
    for raw, problem in (("", "Enter a folder"), ("relative/path", "full path"), (str(tmp_path / "missing"), "does not exist"), (str(cfg.data_dir), "outside the data folder")):
        response = unlocked.post("/settings/auto-backup", data={"folder": raw, "keep": "7"})
        assert response.status_code == 400 and problem in response.text
    done = unlocked.post("/settings/auto-backup", data={"folder": str(folder), "keep": "7"})
    assert done.headers["location"] == "/settings?msg=auto-backup-on#backups"
    made = sorted(p.name for p in folder.iterdir())
    assert made == ["foodforthought-backup-20261118-120000.fftbackup"]
    stage = backup.unpack_and_verify(cfg, folder / made[0], PASSWORD)
    assert (stage / "vault.json").exists()
    assert db(unlocked).get_setting("last_backup") == db(unlocked).get_setting("auto_backup_last")
    page = unlocked.get("/settings").text
    assert f"Saving to <code>{folder}</code>, keeping the last 7" in page

    (folder / "my notes.txt").write_text("keep me")
    for _ in range(8):
        unlocked.post("/settings/auto-backup/now")
    names = sorted(p.name for p in folder.iterdir())
    assert len([n for n in names if n.endswith(".fftbackup")]) == 7 and "my notes.txt" in names
    assert "foodforthought-backup-20261118-120000.fftbackup" not in names and not any(n.endswith(".part") for n in names)

    for p in folder.iterdir():
        p.unlink()
    folder.rmdir()
    failed = unlocked.post("/settings/auto-backup/now")
    assert failed.status_code == 400 and "does not exist" in failed.text
    assert "The last automatic backup failed" in unlocked.get("/settings").text
    assert db(unlocked).one("SELECT count(*) FROM audit_log WHERE action = 'auto_backup_failed'")[0] == 1

    unlocked.post("/settings/auto-backup/off")
    assert autobackup.settings(db(unlocked))["folder"] == "" and "Turn on and back up now" in unlocked.get("/settings").text


def test_schedule(unlocked, cfg, tmp_path, numbered):
    folder = tmp_path / "Backups"
    folder.mkdir()
    database = db(unlocked)
    assert not autobackup.due(database)
    database.set_setting("auto_backup_dir", str(folder))
    assert autobackup.due(database)
    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    database.set_setting("auto_backup_last", (now - timedelta(hours=23)).isoformat())
    assert not autobackup.due(database, now)
    database.set_setting("auto_backup_last", (now - timedelta(hours=25)).isoformat())
    assert autobackup.due(database, now)
    scheduler = autobackup.Scheduler(cfg, core_of(unlocked))
    scheduler.run_if_due()
    assert len(list(folder.iterdir())) == 1
    scheduler.run_if_due()
    assert len(list(folder.iterdir())) == 1
    core_of(unlocked).lock("test")
    autobackup.Scheduler(cfg, core_of(unlocked)).run_if_due()


def test_suggested_folders(tmp_path):
    (tmp_path / "GoogleDrive" / "MyDrive" / "Backups").mkdir(parents=True)
    (tmp_path / "MyFiles" / "Downloads" / "FoodForThought backups").mkdir(parents=True)
    (tmp_path / "MyFiles" / ".hidden").mkdir()
    found = autobackup.suggested_folders(tmp_path)
    paths = [f["path"] for f in found]
    assert str(tmp_path / "MyFiles" / "Downloads" / "FoodForThought backups") in paths and str(tmp_path / "MyFiles" / ".hidden") not in paths
    assert paths[0] == str(tmp_path / "GoogleDrive" / "MyDrive" / "Backups") and str(tmp_path / "MyFiles") not in paths
    labels = {f["label"] for f in found}
    assert "My files › Downloads › FoodForThought backups" in labels and "Google Drive › My Drive › Backups" in labels
    assert autobackup.suggested_folders(tmp_path / "none") == []


def test_picking_a_suggested_folder(unlocked, tmp_path, monkeypatch, numbered):
    shared = tmp_path / "chromeos"
    (shared / "MyFiles" / "Downloads" / "FoodForThought backups").mkdir(parents=True)
    monkeypatch.setattr(autobackup, "SHARED", shared)
    page = unlocked.get("/settings").text
    target = shared / "MyFiles" / "Downloads" / "FoodForThought backups"
    assert "My files › Downloads › FoodForThought backups" in page and f'name="pick" value="{target}"' in page
    done = unlocked.post("/settings/auto-backup", data={"folder": "", "pick": str(target), "keep": "14"})
    assert done.status_code == 303 and len(list(target.iterdir())) == 1


def test_retention_dates():
    def until(issued, *paid):
        return keep_until({"issue_date": issued, "payments": [{"paid_on": p} for p in paid]})
    assert until("2026-05-01") == date(2033, 1, 31)
    assert until("2026-04-05") == date(2032, 1, 31)
    assert until("2026-04-06") == date(2033, 1, 31)
    assert until("2026-03-20", "2026-04-10") == date(2033, 1, 31)


def test_income_exports(practice):
    jane = add_client(practice)
    first = draft(practice, jane)
    issue(practice, first, lines=(("Initial consultation", "1", "85"), ("=SUM(A1)", "1", "5")), bill_to_name="Jane Smith")
    practice.post(f"/invoices/{first}/payments", data={"paid_on": "12/11/2026", "amount": "50", "method": "Bank transfer"})
    old = draft(practice, jane)
    issue(practice, old, issue_date="01/03/2026", bill_to_name="Jane Smith")
    response = practice.get("/invoices/summary/invoices.csv?year=2026")
    assert response.headers["content-type"].startswith("text/csv") and "invoices-2026-27.csv" in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][:4] == ["Number", "Issue date", "Billed to", "Description"] and len(rows) == 2
    assert rows[1][2] == "Jane Smith" and rows[1][3] == "Initial consultation; =SUM(A1)" and rows[1][6] == "90.00" and rows[1][8] == "50.00"
    payments = list(csv.reader(io.StringIO(practice.get("/invoices/summary/payments.csv?year=2026").text)))
    assert payments[1] == ["2026-11-12", rows[1][0], "Jane Smith", "50.00", "Bank transfer", ""]
    assert len(list(csv.reader(io.StringIO(practice.get("/invoices/summary/invoices.csv?year=2025").text)))) == 2
    assert "Download invoices (spreadsheet)" in practice.get("/invoices/summary").text
    assert db(practice).one("SELECT count(*) FROM audit_log WHERE action = 'income_exported'")[0] == 3


def test_csv_cells_cannot_run_formulas():
    from foodforthought.invoices import csv_text
    assert csv_text([["=SUM(A1)", "+1", "-2", "@x", "fine", 3]]).strip() == "'=SUM(A1),'+1,'-2,'@x,fine,3"


def test_kept_invoices_after_erasure(practice):
    jane = add_client(practice)
    recent, old = draft(practice, jane), draft(practice, jane)
    issue(practice, recent, bill_to_name="Jane Smith")
    issue(practice, old, issue_date="01/05/2015", due_date="15/05/2015", bill_to_name="Jane Smith")
    other = draft(practice)
    issue(practice, other, bill_to_name="Acme Ltd")
    assert "Invoices kept after erasure" not in practice.get("/invoices").text
    practice.post(f"/clients/{jane}/erase", data={"confirm": "Jane Smith"})
    found = kept_invoices(db(practice))
    assert [i["id"] for i in found] == [old, recent] and found[0]["can_delete"] and not found[1]["can_delete"]
    assert db(practice).one("SELECT erased_at FROM invoice WHERE id = ?", (other,))["erased_at"] is None
    assert "Invoices kept after erasure (2)" in practice.get("/invoices").text
    page = practice.get("/invoices/kept").text
    assert "31 Jan 2022" in page and "31 Jan 2033" in page and "Can be deleted" in page
    assert "1 invoice kept for erased clients is past the retention date" in practice.get("/").text
    done = practice.post("/invoices/kept/delete", data={"invoice": [str(old), str(recent)]})
    assert done.headers["location"] == "/invoices/kept?msg=kept-deleted"
    remaining = {row["id"] for row in db(practice).query("SELECT id FROM invoice")}
    assert old not in remaining and recent in remaining and other in remaining
    assert db(practice).one("SELECT count(*) FROM audit_log WHERE action = 'kept_invoices_deleted'")[0] == 1
    assert "past the retention date" not in practice.get("/").text


def test_comfort_settings(unlocked, cfg):
    assert '<html lang="en" class="size-normal spacing-normal">' in unlocked.get("/settings").text
    done = unlocked.post("/settings/comfort", data={"size": "larger", "spacing": "loose"})
    assert done.headers["location"] == "/settings?msg=comfort-saved#comfort"
    assert json.loads((cfg.data_dir / "comfort.json").read_text()) == {"size": "larger", "spacing": "loose"}
    assert 'class="size-larger spacing-loose"' in unlocked.get("/").text
    unlocked.post("/lock")
    assert 'class="size-larger spacing-loose"' in unlocked.get("/unlock").text
    unlocked.post("/unlock", data={"password": PASSWORD})
    unlocked.post("/settings/comfort", data={"size": "huge", "spacing": "x"})
    assert 'class="size-normal spacing-normal"' in unlocked.get("/").text
    (cfg.data_dir / "comfort.json").write_text("not json")
    assert 'class="size-normal spacing-normal"' in unlocked.get("/").text
