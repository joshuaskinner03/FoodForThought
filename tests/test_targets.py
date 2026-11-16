import pytest

from foodforthought.targets import bmr, henry, macros, mifflin

from .conftest import core_of
from .test_clients import add_client, jane  # noqa: F401

FORM = {
    "set_on": "16/11/2026", "sex": "female", "age": "36", "weight_kg": "65", "height_cm": "165",
    "equation": "henry", "activity": "1.55", "adjustment_kcal": "-300",
    "protein_basis": "per_kg", "protein_value": "1.2", "fat_percent": "30",
}


def db(client):
    return core_of(client).db


@pytest.mark.parametrize(
    "sex, age, weight, height, expected",
    [
        ("male", 25, 80, 180, 1828.4), ("male", 45, 80, 180, 1748.8), ("male", 70, 80, 180, 1629.8),
        ("female", 25, 65, 165, 1408.75), ("female", 45, 65, 165, 1348.4), ("female", 70, 65, 165, 1259.15),
        ("male", 29, 80, 180, 1828.4), ("male", 30, 80, 180, 1748.8), ("female", 59, 65, 165, 1348.4), ("female", 60, 65, 165, 1259.15),
    ],
)
def test_henry_matches_hand_calculation(sex, age, weight, height, expected):
    assert henry(sex, weight, height, age) == pytest.approx(expected, abs=0.01)


def test_mifflin_matches_hand_calculation():
    assert mifflin("male", 80, 180, 30) == pytest.approx(1780)
    assert mifflin("female", 65, 165, 40) == pytest.approx(1320.25)
    assert bmr("mifflin", "male", 80, 180, 30) == mifflin("male", 80, 180, 30)
    assert bmr("henry", "male", 80, 180, 30) == henry("male", 80, 180, 30)


def test_macro_split():
    per_kg = macros(2000, 70, "per_kg", 1.0, 35)
    assert per_kg["protein_g"] == pytest.approx(70) and per_kg["protein_percent"] == pytest.approx(14)
    assert per_kg["fat_g"] == pytest.approx(77.78, abs=0.01)
    assert per_kg["carbohydrate_g"] == pytest.approx(272) and per_kg["carbohydrate_percent"] == pytest.approx(51)
    percent = macros(2000, 70, "percent", 20, 30)
    assert percent["protein_g"] == pytest.approx(100) and percent["carbohydrate_g"] == pytest.approx(1000 / 3.75)
    energy = per_kg["protein_g"] * 4 + per_kg["fat_g"] * 9 + per_kg["carbohydrate_g"] * 3.75
    assert energy == pytest.approx(2000)


def test_form_is_prefilled_from_the_record(unlocked, jane):
    unlocked.post(f"/clients/{jane}/measurements/new", data={"measured_on": "01/09/2026", "weight_kg": "66", "height_cm": "165"})
    unlocked.post(f"/clients/{jane}/measurements/new", data={"measured_on": "01/10/2026", "weight_kg": "65"})
    page = unlocked.get(f"/clients/{jane}/targets/new").text
    assert 'name="weight_kg" inputmode="decimal" value="65"' in page and 'name="height_cm" inputmode="decimal" value="165"' in page
    assert 'name="age" inputmode="numeric" value="36"' in page and '<option value="female" selected>' in page
    assert "Weight is from the measurement on 1 Oct 2026." in page


def test_set_targets_and_see_them(unlocked, jane):
    assert unlocked.post(f"/clients/{jane}/targets/new", data=FORM).status_code == 303
    row = db(unlocked).one("SELECT * FROM target")
    base = 8.18 * 65 + 5.02 * 165 - 11.6
    assert row["bmr_kcal"] == pytest.approx(base) and row["energy_kcal"] == pytest.approx(base * 1.55 - 300)
    assert row["protein_g"] == pytest.approx(78) and row["set_on"] == "2026-11-16"
    page = unlocked.get(f"/clients/{jane}/targets").text
    for expected in ("1348 kcal a day", "giving 2090 kcal a day", "-300 kcal a day", "<strong>1790 kcal a day</strong>", "<strong>78 g</strong>", "1.2 g per kg", "<strong>60 g</strong>", "Henry (Oxford, 2005)"):
        assert expected in page, expected

    unlocked.post(f"/clients/{jane}/targets/new", data={**FORM, "set_on": "17/11/2026", "equation": "mifflin", "activity": "custom", "custom_pal": "1.63", "adjustment_kcal": "", "protein_basis": "percent", "protein_value": "20"})
    rows = db(unlocked).query("SELECT * FROM target ORDER BY set_on")
    assert rows[1]["pal"] == 1.63 and rows[1]["adjustment_kcal"] == 0 and rows[1]["bmr_kcal"] == pytest.approx(650 + 1031.25 - 180 - 161)
    page = unlocked.get(f"/clients/{jane}/targets").text
    assert page.index("Current, set 17 Nov 2026") < page.index("Earlier, set 16 Nov 2026")
    again = unlocked.get(f"/clients/{jane}/targets/new").text
    assert '<option value="custom" selected>' in again and 'name="custom_pal" inputmode="decimal" value="1.63"' in again

    other = add_client(unlocked, first_name="Omar", last_name="Khan")
    assert unlocked.post(f"/clients/{other}/targets/{rows[0]['id']}/delete").status_code == 404
    unlocked.post(f"/clients/{jane}/targets/{rows[0]['id']}/delete")
    assert db(unlocked).one("SELECT count(*) FROM target")[0] == 1


def test_target_validation(unlocked, jane):
    url = f"/clients/{jane}/targets/new"
    bad = [
        {"sex": ""}, {"age": "17"}, {"age": "old"}, {"weight_kg": "5"}, {"height_cm": "1.65"}, {"set_on": "bad"},
        {"activity": "custom", "custom_pal": "3"}, {"activity": "custom", "custom_pal": ""}, {"adjustment_kcal": "-5000"},
        {"protein_value": "9"}, {"fat_percent": "90"}, {"adjustment_kcal": "-1500"},
        {"protein_basis": "percent", "protein_value": "50", "fat_percent": "60"},
    ]
    for change in bad:
        response = unlocked.post(url, data={**FORM, **change})
        assert response.status_code == 400, change
    kept = unlocked.post(url, data={**FORM, "weight_kg": "heavy"}).text
    assert 'value="heavy"' in kept and 'value="1.2"' in kept
    assert db(unlocked).one("SELECT count(*) FROM target")[0] == 0


def test_targets_are_exported_and_erased(unlocked, jane):
    import io
    import json
    import zipfile

    unlocked.post(f"/clients/{jane}/targets/new", data=FORM)
    archive = zipfile.ZipFile(io.BytesIO(unlocked.post(f"/clients/{jane}/export").content))
    record = json.loads(archive.read("record.json"))
    assert len(record["targets"]) == 1 and record["targets"][0]["equation"] == "henry"
    assert "Energy and macronutrient targets" in archive.read("record.html").decode()
    unlocked.post(f"/clients/{jane}/erase", data={"confirm": "Jane Smith"})
    assert db(unlocked).one("SELECT count(*) FROM target")[0] == 0
