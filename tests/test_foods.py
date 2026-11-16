import pytest

from foodforthought import cofid
from foodforthought.cofid import parse, parse_value
from foodforthought.nutrients import NUTRIENTS, energy_from_macros

from .cofid_sample import workbook
from .conftest import core_of


def db(client):
    return core_of(client).db


def upload(client, data: bytes, name="CoFID.xlsx"):
    return client.post("/foods/import", files={"file": (name, data, "application/vnd.ms-excel")})


@pytest.fixture
def imported(unlocked):
    assert upload(unlocked, workbook()).headers["location"] == "/foods/import?msg=imported"
    return unlocked


def food_id(client, code):
    return db(client).one("SELECT id FROM food WHERE code = ?", (code,))["id"]


def test_parse_value():
    assert parse_value(1.5) == (1.5, "") and parse_value(0) == (0.0, "") and parse_value("42.0") == (42.0, "")
    assert parse_value("Tr") == (0.0, "trace") and parse_value(" tr ") == (0.0, "trace") and parse_value("(Tr)") == (0.0, "trace")
    assert parse_value("N") == (None, "unknown") and parse_value(None) == (None, "blank") and parse_value("  ") == (None, "blank")
    assert parse_value("(7.0)") == (7.0, "estimated") and parse_value("[1.2]") == (1.2, "estimated")
    assert parse_value("<0.1") == (0.1, "") and parse_value("1,234.5") == (1234.5, "")
    assert parse_value("see footnote") == (None, "unreadable") and parse_value(True) == (None, "unknown")


def test_nutrient_catalogue_is_consistent():
    assert len({n.key for n in NUTRIENTS}) == len(NUTRIENTS) == len({n.code for n in NUTRIENTS})
    assert energy_from_macros({"protein": 10, "fat": 10, "carbohydrate": 10, "alcohol": 10}) == 40 + 90 + 37.5 + 70
    assert energy_from_macros({"protein": 10, "fat": 10, "carbohydrate": 10}) == 167.5
    assert energy_from_macros({"protein": 10, "fat": None, "carbohydrate": 10}) is None


@pytest.mark.parametrize(
    "options, matched_by",
    [
        ({}, "codes"),
        ({"numbered": False, "code_row": 3}, "codes"),
        ({"code_row": 1}, "codes"),
        ({"code_row": None}, "column titles"),
        ({"extra_top_rows": 2}, "codes"),
    ],
)
def test_parse_copes_with_layout_variations(options, matched_by):
    foods, report = parse(workbook(**options))
    by_code = {food.code: food for food in foods}
    assert set(by_code) == {"11-001", "12-001", "17-001", "14-001", "99-999"}
    assert [sheet["matched_by"] for sheet in report["sheets"]] == [matched_by] * 3
    assert "Fraction" not in report["sheets"][2]["found"]
    assert report["sheets"][0]["first_data_row"] == 4 + options.get("extra_top_rows", 0)

    milk = by_code["12-001"]
    assert milk.name == "Milk, semi-skimmed, pasteurised, average" and milk.group == "BA"
    assert milk.nutrients["protein"] == 3.5 and milk.nutrients["energy_kcal"] == 46 and milk.nutrients["calcium"] == 120
    assert milk.nutrients["vitamin_b12"] == 0.9 and milk.nutrients["vitamin_a"] == 20 and milk.nutrients["retinol"] == 19
    assert milk.nutrients["niacin"] == 0.1 and milk.nutrients["niacin_equivalent"] == 0.9
    assert milk.nutrients["riboflavin"] == 0.24 and milk.flags["riboflavin"] == "estimated"
    assert milk.nutrients["vitamin_d"] == 0 and milk.flags["vitamin_d"] == "trace"

    porridge = by_code["11-001"]
    assert porridge.description == "Rolled oats and water"
    assert porridge.nutrients["sugars"] == 0 and porridge.flags["sugars"] == "trace"
    assert porridge.nutrients["iodine"] is None and porridge.flags["iodine"] == "unknown"
    assert porridge.nutrients["fibre"] == 1.1 and porridge.nutrients["fibre_nsp"] == 0.8

    wine = by_code["17-001"]
    assert wine.nutrients["alcohol"] == 9.6 and wine.nutrients["fibre"] is None and "sodium" not in wine.nutrients
    bread = by_code["14-001"]
    assert bread.nutrients["carbohydrate"] == 42.0 and bread.nutrients["fibre"] == 7.0 and bread.flags["fibre"] == "estimated"

    assert report["foods"] == 5 and report["skipped_rows"] == 2
    assert report["energy_checked"] == 5 and report["energy_mismatched"] == 1
    assert "Zinc" in report["nutrients_missing"] and "Protein" not in report["nutrients_missing"]


def test_parse_rejects_other_files():
    from openpyxl import Workbook
    import io

    with pytest.raises(cofid.ImportProblem, match="could not be opened"):
        parse(b"this is not a workbook")
    book = Workbook()
    book.active.title = "Budget"
    out = io.BytesIO()
    book.save(out)
    with pytest.raises(cofid.ImportProblem, match="Proximates"):
        parse(out.getvalue())
    book = Workbook()
    book.active.title = "Proximates"
    book.active.append(["Just", "some", "words"])
    out = io.BytesIO()
    book.save(out)
    with pytest.raises(cofid.ImportProblem, match="nutrient columns"):
        parse(out.getvalue())


def test_import_page_before_and_after(unlocked):
    assert "has not been imported yet" in unlocked.get("/foods").text
    before = unlocked.get("/foods/import").text
    assert "gov.uk/government/publications/composition-of-foods-integrated-dataset-cofid" in before
    for bad in (b"", b"not a workbook"):
        response = upload(unlocked, bad)
        assert response.status_code == 400 and "Nothing was changed" in response.text
    assert db(unlocked).one("SELECT count(*) FROM food")[0] == 0

    assert upload(unlocked, workbook()).status_code == 303
    report = unlocked.get("/foods/import").text
    assert "5 read from the file: 5 new, 0 updated" in report and "4 of 5 foods have an energy value that agrees" in report
    assert "That is too few" in report and "Open Government Licence" in report and "matched by codes" in report
    assert "has not been imported yet" not in unlocked.get("/foods").text
    assert db(unlocked).one("SELECT count(*) FROM audit_log WHERE action = 'food_database_imported'")[0] == 1


def test_reimport_updates_in_place_and_keeps_portions(imported):
    milk = food_id(imported, "12-001")
    imported.post(f"/foods/{milk}/portions", data={"name": "1 glass", "grams": "200"})
    assert upload(imported, workbook()).status_code == 303
    assert food_id(imported, "12-001") == milk
    assert db(imported).one("SELECT count(*) FROM food")[0] == 5
    assert db(imported).one("SELECT count(*) FROM portion")[0] == 1
    assert "5 read from the file: 0 new, 5 updated" in imported.get("/foods/import").text


def test_search_ranks_and_filters(imported):
    def names(q):
        page = imported.get(f"/foods?q={q}", headers={"HX-Request": "true"}).text
        assert "<html" not in page
        return page

    assert "Milk, semi-skimmed" in names("milk") and "Porridge" not in names("milk")
    assert "Milk, semi-skimmed" in names("skimmed milk") and "Milk, semi-skimmed" in names("MILK semi")
    assert "Porridge" in names("11-001")
    assert "No foods match" in names("zzzz") and "No foods match" in names("%25")
    both = names("average")
    assert both.index("Bread, wholemeal") < both.index("Milk, semi-skimmed")
    assert "Type in the box" in imported.get("/foods").text


def test_food_page_scales_and_marks_gaps(imported):
    milk = food_id(imported, "12-001")
    page = imported.get(f"/foods/{milk}").text
    assert "Nutrients in 100 g" in page and "CoFID 2021, code 12-001" in page and "Milk and milk products" in page
    assert "46 kcal" in page and "3.5 g" in page and "120 mg" in page and "0.24* mg" in page and ">trace<" in page
    glass = imported.get(f"/foods/{milk}?grams=250").text
    assert "Nutrients in 250 g" in glass and "115 kcal" in glass and "8.8 g" in glass and "300 mg" in glass
    assert "Nutrients in 100 g" in imported.get(f"/foods/{milk}?grams=banana").text
    porridge = imported.get(f"/foods/{food_id(imported, '11-001')}").text
    assert "not known" in porridge
    assert imported.get("/foods/99999").status_code == 404


def test_portions(imported):
    milk = food_id(imported, "12-001")
    for bad in ({"name": "", "grams": "200"}, {"name": "glass", "grams": "0"}, {"name": "glass", "grams": "lots"}):
        assert imported.post(f"/foods/{milk}/portions", data=bad).headers["location"].endswith("err=portion")
    imported.post(f"/foods/{milk}/portions", data={"name": "1 glass", "grams": "200"})
    page = imported.get(f"/foods/{milk}").text
    assert "1 glass (200 g)" in page and f"/foods/{milk}?grams=200" in page
    portion_id = db(imported).one("SELECT id FROM portion")["id"]
    imported.post(f"/foods/{milk}/portions/{portion_id}/delete")
    assert db(imported).one("SELECT count(*) FROM portion")[0] == 0


def test_custom_food_create_edit_hide(imported):
    assert imported.post("/foods/new", data={"name": "", "n_energy_kcal": "100"}).status_code == 400
    assert imported.post("/foods/new", data={"name": "Bar"}).status_code == 400
    kept = imported.post("/foods/new", data={"name": "Bar", "n_energy_kcal": "lots"})
    assert kept.status_code == 400 and 'value="lots"' in kept.text
    done = imported.post("/foods/new", data={"name": "Oat milk, Brand X", "description": "From the carton", "n_energy_kcal": "46", "n_protein": "1,0", "n_fat": "1.5", "n_carbohydrate": "6.7", "n_calcium": "120"})
    custom = int(done.headers["location"].split("/")[2].split("?")[0])
    row = db(imported).one("SELECT * FROM food WHERE id = ?", (custom,))
    assert '"protein": 1.0' in row["nutrients"] and '"iron"' not in row["nutrients"]
    listing = imported.get("/foods?q=milk").text
    assert listing.index("Oat milk, Brand X") < listing.index("Milk, semi-skimmed") and "Mine" in listing
    assert "My foods" in imported.get("/foods").text

    imported.post(f"/foods/{custom}/edit", data={"name": "Oat drink, Brand X", "n_energy_kcal": "48"})
    assert "48 kcal" in imported.get(f"/foods/{custom}").text
    imported.post(f"/foods/{custom}/archive")
    assert "Oat drink" not in imported.get("/foods?q=oat").text and "Hidden" in imported.get("/foods").text
    imported.post(f"/foods/{custom}/archive", data={"restore": "1"})
    assert "Oat drink" in imported.get("/foods?q=oat").text

    milk = food_id(imported, "12-001")
    assert imported.get(f"/foods/{milk}/edit").status_code == 409
    assert imported.post(f"/foods/{milk}/edit", data={"name": "Hacked", "n_energy_kcal": "1"}).status_code == 409
    assert imported.post(f"/foods/{milk}/archive").status_code == 409
    upload(imported, workbook())
    assert db(imported).one("SELECT name FROM food WHERE id = ?", (custom,))["name"] == "Oat drink, Brand X"


def test_food_pages_need_unlocking_and_survive_backup(imported):
    from .conftest import PASSWORD
    from .test_backup import restore

    data = imported.post("/backup").content
    assert b"Porridge" not in data
    assert restore(imported, data).status_code == 303
    for path in ("/foods", "/foods/import", "/foods/new"):
        assert imported.get(path).headers["location"] == "/unlock"
    imported.post("/unlock", data={"password": PASSWORD})
    assert "Porridge" in imported.get("/foods?q=porridge").text
