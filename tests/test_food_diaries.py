import io
import json
import zipfile

import pytest

from foodforthought import analysis, reference
from foodforthought.matching import text_key

from .conftest import core_of
from .test_clients import add_client
from .test_diary_import import CRONOMETER_NEW
from .test_foods import imported, upload  # noqa: F401

HX = {"HX-Request": "true"}


def db(client):
    return core_of(client).db


def food(client, code):
    return db(client).one("SELECT id FROM food WHERE code = ?", (code,))["id"]


@pytest.fixture
def jane(imported):
    return add_client(imported)


def new_diary(client, client_id, start="10/11/2026", days="3") -> int:
    done = client.post(f"/clients/{client_id}/diaries/new", data={"title": "Week one", "start_date": start, "days": days})
    assert done.status_code == 303, done.text
    return int(done.headers["location"].rsplit("/", 1)[1])


def import_file(client, client_id, data, name="servings.csv"):
    return client.post(f"/clients/{client_id}/diaries/import", files={"file": (name, data, "text/csv")})


def test_analysis_arithmetic():
    a = {"energy_kcal": 100, "protein": 10, "fat": 4, "saturates": 2, "carbohydrate": 8, "sugars": 4, "fibre": 5, "alcohol": 0,
         "sodium": 200, "calcium": 300, "iron": 5, "vitamin_c": 20, "folate": 100, "potassium": 2000}
    b = {"energy_kcal": 200, "protein": 5, "fat": 10, "saturates": 5, "carbohydrate": 20, "sugars": 10, "fibre": 2, "alcohol": 0,
         "sodium": 100, "calcium": 50, "iron": 1, "vitamin_c": 0, "folate": 50, "potassium": 3000}
    entries = [{"day": "2026-11-10", "grams": 200, "nutrients": json.dumps(a)}, {"day": "2026-11-11", "grams": 100, "nutrients": json.dumps(b)}]
    group = reference.group_for("female", 30)
    result = analysis.analyse(entries, ["2026-11-10", "2026-11-11", "2026-11-12"], group, 60, None)
    assert result["day_count"] == 2 and result["too_few_days"] and result["empty_days"] == ["2026-11-12"]
    avg = result["average"]
    assert avg["energy_kcal"] == 200 and avg["protein"] == 12.5 and avg["calcium"] == 325 and avg["iron"] == 5.5
    macros = {m["key"]: m for m in result["macros"]}
    assert macros["protein"]["share"] == pytest.approx(25) and macros["protein"]["state"] == "below"
    assert "45.0 g" in macros["protein"]["guidance"]
    assert macros["fat"]["share"] == pytest.approx(40.5) and macros["fat"]["state"] == "above"
    assert macros["saturates"]["share"] == pytest.approx(20.25) and macros["saturates"]["state"] == "above"
    assert macros["carbohydrate"]["share"] == pytest.approx(33.75) and macros["carbohydrate"]["state"] == "below"
    assert macros["fibre"]["state"] == "below" and macros["sodium"]["value"] == pytest.approx(0.625) and macros["sodium"]["state"] == "within"
    micros = {m["key"]: m for m in result["micros"]}
    assert micros["calcium"]["state"] == "below_lrni" and micros["iron"]["state"] == "below_lrni"
    assert micros["vitamin_c"]["state"] == "below_rni" and micros["folate"]["state"] == "below_rni"
    assert micros["potassium"]["state"] == "meets" and micros["potassium"]["percent_rni"] == pytest.approx(100)
    assert micros["iodine"]["state"] == "not_assessed" and micros["iodine"]["gap_count"] == 2
    assert micros["copper"]["lrni"] is None
    assert result["per_day"][0]["energy_kcal"] == 200 and result["per_day"][1]["protein"] == 5


def test_missing_values_block_low_intake_flags():
    full = {"energy_kcal": 100, "calcium": 100}
    blank = {"energy_kcal": 100}
    group = reference.group_for("female", 30)
    small_gap = analysis.analyse(
        [{"day": "2026-11-10", "grams": 90, "nutrients": full}, {"day": "2026-11-10", "grams": 10, "nutrients": blank}], [], group, None, None
    )
    calcium = next(m for m in small_gap["micros"] if m["key"] == "calcium")
    assert calcium["gap_share"] == pytest.approx(0.1) and calcium["state"] == "below_lrni"
    big_gap = analysis.analyse(
        [{"day": "2026-11-10", "grams": 70, "nutrients": full}, {"day": "2026-11-10", "grams": 30, "nutrients": blank}], [], group, None, None
    )
    assert next(m for m in big_gap["micros"] if m["key"] == "calcium")["state"] == "not_assessed"
    nothing = analysis.analyse([], [], None, None, None)
    assert nothing["day_count"] == 0 and all(m["state"] == "no_reference" for m in nothing["micros"])


def test_manual_diary_entry(imported, jane):
    diary = new_diary(imported, jane)
    page = imported.get(f"/diaries/{diary}").text
    assert 'hx-target="#food-choices" hx-swap="innerHTML"' in page
    assert "Week one" in page and "Day 1, Tue 10 Nov 2026" in page and "Day 3, Thu 12 Nov 2026" in page
    milk = food(imported, "12-001")
    choices = imported.get(f"/diaries/{diary}/food-choices?q=milk%20&day=2026-11-11&meal=Evening%20meal", headers=HX).text
    assert "Milk, semi-skimmed" in choices and "<html" not in choices and f"entry-form?food={milk}" in choices
    assert 'data-q="milk"' in choices and f"?food={milk}&amp;day=2026-11-11&amp;meal=Evening%20meal" in choices
    form = imported.get(f"/diaries/{diary}/entry-form?food={milk}&day=2026-11-11&meal=Lunch", headers=HX).text
    assert "46 kcal per 100 g" in form and 'value="2026-11-11" selected' in form and 'value="Lunch" selected' in form

    done = imported.post(f"/diaries/{diary}/entries", data={"day": "2026-11-10", "meal": "Breakfast", "food_id": milk, "amount": "200", "unit": "g"})
    assert done.status_code == 303 and "day=2026-11-10" in done.headers["location"]
    imported.post(f"/foods/{milk}/portions", data={"name": "1 glass", "grams": "250"})
    portion = db(imported).one("SELECT id FROM portion")["id"]
    panel = imported.post(f"/diaries/{diary}/entries", data={"day": "2026-11-10", "meal": "Lunch", "food_id": milk, "amount": "1.5", "unit": f"portion:{portion}"}, headers=HX)
    assert panel.status_code == 200 and "<html" not in panel.text and 'id="entry-panel"' in panel.text
    assert "1.5 × 1 glass (375 g)" in panel.text and "92 kcal" in panel.text and "264 kcal" in panel.text
    rows = db(imported).query("SELECT * FROM diary_entry ORDER BY id")
    assert [(r["grams"], r["quantity"], r["unit"], r["status"]) for r in rows] == [(200, None, "g", "confirmed"), (375, 1.5, "1 glass", "confirmed")]

    bad = imported.post(f"/diaries/{diary}/entries", data={"day": "2026-11-10", "meal": "Lunch", "food_id": "", "amount": "1"}, headers=HX)
    assert bad.status_code == 400 and "Choose a food first." in bad.text
    assert imported.post(f"/diaries/{diary}/entries", data={"day": "2026-11-10", "food_id": milk, "amount": "0"}, headers=HX).status_code == 400
    assert imported.post(f"/diaries/{diary}/entries", data={"day": "2026-11-10", "food_id": milk, "amount": "6000"}, headers=HX).status_code == 400

    imported.post(f"/diaries/{diary}/entries/{rows[1]['id']}/edit", data={"day": "2026-11-11", "meal": "Lunch", "grams": "500"})
    edited = db(imported).one("SELECT * FROM diary_entry WHERE id = ?", (rows[1]["id"],))
    assert edited["grams"] == 500 and edited["quantity"] == 2 and edited["day"] == "2026-11-11"
    removed = imported.post(f"/diaries/{diary}/entries/{rows[0]['id']}/delete", headers=HX)
    assert removed.status_code == 200 and db(imported).one("SELECT count(*) FROM diary_entry")[0] == 1
    assert imported.get(f"/diaries/{diary}/entries/{rows[0]['id']}/edit").status_code == 404
    assert imported.get("/diaries/9999").status_code == 404


def test_photo_beside_the_entries(imported, jane):
    diary = new_diary(imported, jane)
    imported.post(f"/clients/{jane}/attachments", files={"file": ("blood test.png", b"\x89PNG fake", "image/png")}, data={"description": "Lab"})
    page = imported.get(f"/diaries/{diary}").text
    assert "No photo of this diary yet" in page and "Other images in the client's files" in page and "blood test.png" in page
    done = imported.post(f"/diaries/{diary}/photo", files={"file": ("page1.jpg", b"\xff\xd8 fake jpeg", "image/jpeg")})
    attachment = db(imported).one("SELECT * FROM attachment WHERE diary_id = ?", (diary,))
    assert done.headers["location"] == f"/diaries/{diary}?photo={attachment['id']}&msg=uploaded"
    assert attachment["description"] == "Food diary: Week one" and attachment["client_id"] == jane
    imported.post(f"/diaries/{diary}/photo", files={"file": ("page2.jpg", b"\xff\xd8 fake jpeg 2", "image/jpeg")})
    page = imported.get(f"/diaries/{diary}").text
    assert f'src="/clients/{jane}/attachments/{attachment["id"]}?view=1"' in page and 'title="page2.jpg"' in page
    other = new_diary(imported, jane)
    assert "No photo of this diary yet" in imported.get(f"/diaries/{other}").text
    imported.post(f"/diaries/{diary}/delete")
    assert db(imported).one("SELECT count(*) FROM attachment WHERE diary_id IS NULL")[0] == 3
    assert imported.post(f"/diaries/{other}/photo").headers["location"].endswith("err=no-file")


def test_template_download(imported, jane):
    response = imported.get(f"/clients/{jane}/diaries/template?start=17/11/2026&days=5")
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert b"Smith" not in response.content and b"Jane" not in response.content
    assert db(imported).one("SELECT detail FROM audit_log WHERE action = 'diary_template_downloaded'")["detail"] == "5 days from 2026-11-17"


def test_import_errors_change_nothing(imported, jane):
    for name, data, message in (
        ("x.txt", b"hello", "not recognised"),
        ("x.csv", b"", "Choose the file first"),
        ("servings.csv", b"Day,Group,Food Name,Amount\n", "No foods were found"),
    ):
        response = import_file(imported, jane, data, name)
        assert response.status_code == 400 and message in response.text and "Nothing was imported" in response.text
    assert db(imported).one("SELECT count(*) FROM food_diary")[0] == 0


def test_import_review_and_remember(imported, jane):
    done = import_file(imported, jane, CRONOMETER_NEW.encode())
    diary = int(done.headers["location"].split("/")[2])
    assert done.headers["location"].endswith("/review?msg=diary-imported")
    entries = db(imported).query("SELECT * FROM diary_entry ORDER BY position")
    assert [e["status"] for e in entries] == ["pending"] * 4
    assert db(imported).one("SELECT * FROM food_diary")["start_date"] == "2026-11-10"
    milk_entry, oats_entry, bread_entry, wine_entry = (e["id"] for e in entries)
    milk, bread, porridge = food(imported, "12-001"), food(imported, "14-001"), food(imported, "11-001")
    assert json.loads(entries[0]["suggestions"])[0]["food_id"] == milk

    review = imported.get(f"/diaries/{diary}/review").text
    assert "Milk, semi-skimmed" in review and "200.00 ml" in review and "Millilitres are counted as grams." in review
    assert 'value="200"' in review and "4 to check" in review
    weak = int(import_file(imported, jane, b"Day,Group,Food Name,Amount\n2026-11-12,Lunch,Quinoa salad,100 g\n").headers["location"].split("/")[2])
    weak_review = imported.get(f"/diaries/{weak}/review").text
    assert "No close match" in weak_review and " checked" not in weak_review.split("Matching food")[1].split("</fieldset>")[0]
    assert "still need their food match checked" in imported.get(f"/diaries/{diary}/analysis").text

    row = imported.post(f"/diaries/{diary}/entries/{milk_entry}/confirm", data={"food_id": milk, "grams": "200"}, headers=HX)
    assert row.status_code == 200 and "Confirmed" in row.text and "92 kcal" in row.text and "<html" not in row.text
    switched = imported.get(f"/diaries/{diary}/entries/{oats_entry}/row?food={porridge}", headers=HX).text
    assert f'value="{porridge}" form="confirm-{oats_entry}" checked' in switched or "checked" in switched
    searched = imported.get(f"/diaries/{diary}/entries/{oats_entry}/row?q=porridge", headers=HX).text
    assert "Porridge, made with water" in searched
    bad = imported.post(f"/diaries/{diary}/entries/{oats_entry}/confirm", data={"food_id": porridge, "grams": ""}, headers=HX)
    assert bad.status_code == 400 and "Enter the weight in grams." in bad.text
    imported.post(f"/diaries/{diary}/entries/{oats_entry}/confirm", data={"food_id": porridge, "grams": "240"})
    imported.post(
        f"/diaries/{diary}/entries/{bread_entry}/confirm",
        data={"food_id": bread, "grams": "72", "save_portion": "on", "portion_name": "1 slice"},
        headers=HX,
    )
    assert tuple(db(imported).one("SELECT name, grams FROM portion WHERE food_id = ?", (bread,))) == ("1 slice", 36.0)
    skipped = imported.post(f"/diaries/{diary}/entries/{wine_entry}/skip", headers=HX)
    assert "Skipped" in skipped.text
    assert db(imported).one("SELECT count(*) FROM audit_log WHERE action = 'diary_review_completed'")[0] == 1
    assert "Nothing left to check" in imported.get(f"/diaries/{diary}/review").text

    analysis_page = imported.get(f"/diaries/{diary}/analysis")
    assert analysis_page.status_code == 200 and "Fewer than 3 days" in analysis_page.text
    assert "Female, aged 36" in analysis_page.text and "1 skipped" in analysis_page.text

    reopened = imported.post(f"/diaries/{diary}/entries/{wine_entry}/reopen", headers=HX)
    assert "Confirm" in reopened.text and db(imported).one("SELECT status FROM diary_entry WHERE id = ?", (wine_entry,))["status"] == "pending"
    imported.post(f"/diaries/{diary}/entries/{wine_entry}/skip")

    assert text_key("Milk, semi-skimmed") in {r["text_key"] for r in db(imported).query("SELECT text_key FROM food_match")}
    second = int(import_file(imported, jane, CRONOMETER_NEW.encode()).headers["location"].split("/")[2])
    review = imported.get(f"/diaries/{second}/review").text
    assert review.count("Remembered") == 3 and "Confirm the 2 entries that have a remembered match and a known weight" in review
    assert imported.post(f"/diaries/{second}/review/confirm-remembered").headers["location"].endswith("confirmed=2")
    states = db(imported).query("SELECT status, grams, food_id FROM diary_entry WHERE diary_id = ? ORDER BY position", (second,))
    assert [(s["status"], s["grams"]) for s in states] == [("confirmed", 200), ("pending", None), ("confirmed", 72), ("pending", None)]


def test_analysis_page_and_life_stage(imported, jane):
    diary = new_diary(imported, jane)
    milk, bread = food(imported, "12-001"), food(imported, "14-001")
    imported.post(f"/diaries/{diary}/entries", data={"day": "2026-11-10", "meal": "Breakfast", "food_id": milk, "amount": "400"})
    imported.post(f"/diaries/{diary}/entries", data={"day": "2026-11-11", "meal": "Lunch", "food_id": bread, "amount": "200"})
    page = imported.get(f"/diaries/{diary}/analysis").text
    assert "309 kcal" in page and "2 days with food recorded" in page and "No food is recorded on 12 Nov 2026" in page
    assert "Not assessed: too many foods have no value" in page and "At or above the RNI" in page
    assert "Few foods contain vitamin D" in page and "heavy periods" in page and "folic acid" not in page

    imported.post(f"/clients/{jane}/targets/new", data={
        "set_on": "01/11/2026", "sex": "female", "age": "36", "weight_kg": "65", "height_cm": "165", "equation": "henry",
        "activity": "1.55", "adjustment_kcal": "0", "protein_basis": "per_kg", "protein_value": "1", "fat_percent": "35",
    })
    with_target = imported.get(f"/diaries/{diary}/analysis").text
    assert "Set 1 Nov 2026" in with_target and "<th>Target</th>" in with_target

    settings = imported.get(f"/diaries/{diary}/settings").text
    assert "Pregnant, third trimester" in settings
    imported.post(f"/diaries/{diary}/settings", data={"title": "Week one", "start_date": "10/11/2026", "days": "3", "life_stage": "pregnant_late"})
    pregnant = imported.get(f"/diaries/{diary}/analysis").text
    assert "pregnant, third trimester" in pregnant and "folic acid" in pregnant and "†" in pregnant
    assert ">300<" in pregnant
    assert imported.post(f"/diaries/{diary}/settings", data={"title": "x", "start_date": "bad", "days": "3"}).status_code == 400

    male = add_client(imported, first_name="Omar", last_name="Khan", sex="male")
    male_diary = new_diary(imported, male)
    assert "When this diary was kept" not in imported.get(f"/diaries/{male_diary}/settings").text
    nosex = add_client(imported, first_name="No", last_name="Sex", sex="", date_of_birth="")
    blank = new_diary(imported, nosex)
    imported.post(f"/diaries/{blank}/entries", data={"day": "2026-11-10", "meal": "Lunch", "food_id": milk, "amount": "100"})
    assert "the client's sex is not recorded" in imported.get(f"/diaries/{blank}/analysis").text


def test_diaries_tab_settings_and_delete(imported, jane):
    assert imported.post(f"/clients/{jane}/diaries/new", data={"start_date": "bad", "days": "3"}).headers["location"].endswith("err=diary-days")
    assert imported.post(f"/clients/{jane}/diaries/new", data={"start_date": "10/11/2026", "days": "40"}).headers["location"].endswith("err=diary-days")
    diary = new_diary(imported, jane)
    import_file(imported, jane, CRONOMETER_NEW.encode())
    tab = imported.get(f"/clients/{jane}/diaries").text
    assert "Week one" in tab and "4 to review" in tab and "Cronometer" in tab and "Typed in" in tab
    imported.post(f"/diaries/{diary}/delete")
    assert db(imported).one("SELECT count(*) FROM food_diary")[0] == 1


def test_export_and_erase(imported, jane):
    other = add_client(imported, first_name="Omar", last_name="Khan")
    for client_id in (jane, other):
        diary = int(import_file(imported, client_id, CRONOMETER_NEW.encode()).headers["location"].split("/")[2])
        first = db(imported).one("SELECT id FROM diary_entry WHERE diary_id = ? ORDER BY position", (diary,))["id"]
        imported.post(f"/diaries/{diary}/entries/{first}/confirm", data={"food_id": food(imported, "12-001"), "grams": "200"})
    jane_only = "Jane's special oat bake"
    diary = int(import_file(imported, jane, f"Day,Group,Food Name,Amount\n2026-11-12,Lunch,{jane_only},100 g\n".encode()).headers["location"].split("/")[2])
    entry = db(imported).one("SELECT id FROM diary_entry WHERE diary_id = ?", (diary,))["id"]
    imported.post(f"/diaries/{diary}/entries/{entry}/confirm", data={"food_id": food(imported, "11-001"), "grams": "100"})
    assert db(imported).one("SELECT count(*) FROM food_match")[0] == 2

    archive = zipfile.ZipFile(io.BytesIO(imported.post(f"/clients/{jane}/export").content))
    record = json.loads(archive.read("record.json"))
    assert len(record["food_diaries"]) == 2 and record["food_diaries"][0]["entries"][0]["food"] == "Milk, semi-skimmed, pasteurised, average"
    assert "Food diaries" in archive.read("record.html").decode() and "special oat bake" in archive.read("record.html").decode()
    assert "food diaries: 2" in imported.get(f"/clients/{jane}/data").text

    imported.post(f"/clients/{jane}/erase", data={"confirm": "Jane Smith"})
    assert db(imported).one("SELECT count(*) FROM food_diary")[0] == 1
    assert db(imported).one("SELECT count(*) FROM diary_entry WHERE diary_id NOT IN (SELECT id FROM food_diary)")[0] == 0
    keys = {r["text_key"] for r in db(imported).query("SELECT text_key FROM food_match")}
    assert keys == {text_key("Milk, semi-skimmed")}
