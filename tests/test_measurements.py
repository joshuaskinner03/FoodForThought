from datetime import date

import pytest

from foodforthought.measurements import bmi, body_fat, chart, siri, with_bmi

from .conftest import core_of
from .test_clients import add_client, jane  # noqa: F401

FOUR = {"biceps": 8, "triceps": 12, "subscapular": 10, "suprailiac": 10}
SEVEN = {"chest": 10, "midaxillary": 12, "triceps": 14, "subscapular": 16, "abdomen": 20, "suprailiac": 14, "thigh": 14}


def test_siri_and_bmi():
    assert siri(1.1) == pytest.approx(0.0, abs=1e-9)
    assert siri(1.05) == pytest.approx(21.43, abs=0.01)
    assert bmi(70, 175) == 22.9 and bmi(None, 175) is None and bmi(70, None) is None


@pytest.mark.parametrize(
    "method, sex, age, skinfolds, expected",
    [
        ("durnin_womersley", "male", 25, FOUR, 16.2),
        ("durnin_womersley", "female", 45, {"biceps": 10, "triceps": 20, "subscapular": 15, "suprailiac": 15}, 33.2),
        ("jackson_pollock_3", "male", 30, {"chest": 15, "abdomen": 25, "thigh": 20}, 17.9),
        ("jackson_pollock_3", "female", 30, {"triceps": 20, "suprailiac": 15, "thigh": 25}, 24.1),
        ("jackson_pollock_7", "male", 40, SEVEN, 15.9),
        ("jackson_pollock_7", "female", 40, {**SEVEN, "abdomen": 30, "thigh": 24}, 24.3),
    ],
)
def test_body_fat_matches_hand_calculation(method, sex, age, skinfolds, expected):
    percent, problem = body_fat(method, sex, age, skinfolds)
    assert problem is None and percent == pytest.approx(expected, abs=0.1)


def test_durnin_womersley_age_bands_change_the_result():
    results = [body_fat("durnin_womersley", "male", age, FOUR)[0] for age in (19, 25, 35, 45, 60)]
    assert len(set(results)) == 5 and results[1:] == sorted(results[1:]) and all(10 < r < 30 for r in results)


def test_body_fat_explains_what_is_missing():
    assert body_fat("", "male", 30, FOUR) == (None, None)
    assert "sex" in body_fat("durnin_womersley", "", 30, FOUR)[1]
    assert "date of birth" in body_fat("durnin_womersley", "male", None, FOUR)[1]
    assert "16 and over" in body_fat("durnin_womersley", "male", 12, FOUR)[1]
    assert "Chest" in body_fat("jackson_pollock_3", "male", 30, FOUR)[1]
    assert "Thigh" in body_fat("jackson_pollock_3", "female", 30, FOUR)[1]


def test_bmi_uses_latest_earlier_height():
    rows = [
        {"id": 2, "measured_on": "2026-02-01", "weight_kg": 80.0, "height_cm": None, "skinfolds": "{}"},
        {"id": 1, "measured_on": "2026-01-01", "weight_kg": 82.0, "height_cm": 180.0, "skinfolds": "{}"},
        {"id": 3, "measured_on": "2025-12-01", "weight_kg": 83.0, "height_cm": None, "skinfolds": "{}"},
    ]
    items = with_bmi(rows)
    assert [i["measured_on"] for i in items] == ["2025-12-01", "2026-01-01", "2026-02-01"]
    assert [i["bmi"] for i in items] == [None, 25.3, 24.7]


def test_chart_geometry():
    assert chart([(date(2026, 1, 1), 80.0)], "Weight", "kg") is None
    made = chart([(date(2026, 1, 1), 82.0), (date(2026, 2, 1), 80.5), (date(2026, 3, 1), 79.0)], "Weight", "kg")
    assert len(made["marks"]) == 3 and made["path"].startswith("M") and made["path"].count("L") == 2
    xs = [m["x"] for m in made["marks"]]
    ys = [m["y"] for m in made["marks"]]
    assert xs == sorted(xs) and xs[0] == made["left"] and xs[-1] == made["plot_right"]
    assert ys == sorted(ys)
    assert all(made["ticks"][-1]["y"] <= y <= made["ticks"][0]["y"] for y in ys)
    assert made["last_label"] == "79.0 kg" and made["marks"][0]["tip"] == "1 Jan 2026: 82.0 kg"
    flat = chart([(date(2026, 1, 1), 70.0), (date(2026, 1, 2), 70.0)], "Weight", "kg")
    assert len({t["y"] for t in flat["ticks"]}) >= 2


def test_record_measurements_and_see_them(unlocked, jane):
    url = f"/clients/{jane}/measurements"
    first = unlocked.post(f"{url}/new", data={"measured_on": "01/09/2026", "weight_kg": "72,5", "height_cm": "168", "waist_cm": "80"})
    assert first.status_code == 303
    unlocked.post(
        f"{url}/new",
        data={
            "measured_on": "01/10/2026", "weight_kg": "71", "body_fat_method": "jackson_pollock_3",
            "sf_triceps": "20", "sf_suprailiac": "15", "sf_thigh": "25",
        },
    )
    rows = core_of(unlocked).db.query("SELECT * FROM measurement ORDER BY measured_on")
    assert rows[0]["weight_kg"] == 72.5 and rows[0]["measured_on"] == "2026-09-01"
    assert rows[1]["body_fat_percent"] == pytest.approx(24.5, abs=0.1)
    page = unlocked.get(url).text
    assert "25.7" in page and "25.2" in page
    assert page.count("<svg") == 2 and "1 Sep 2026: 72.5 kg" in page
    assert "<style" not in page and " style=" not in page


def test_measurement_validation(unlocked, jane):
    url = f"/clients/{jane}/measurements/new"
    cases = [
        {"measured_on": "01/09/2026"},
        {"measured_on": "bad", "weight_kg": "70"},
        {"measured_on": "01/01/2999", "weight_kg": "70"},
        {"measured_on": "01/09/2026", "weight_kg": "7000"},
        {"measured_on": "01/09/2026", "weight_kg": "seventy"},
        {"measured_on": "01/09/2026", "sf_triceps": "500"},
        {"measured_on": "01/09/2026", "sf_triceps": "20", "body_fat_method": "jackson_pollock_3"},
    ]
    for data in cases:
        assert unlocked.post(url, data=data).status_code == 400, data
    kept = unlocked.post(url, data={"measured_on": "01/09/2026", "weight_kg": "seventy"}).text
    assert 'value="seventy"' in kept
    assert core_of(unlocked).db.one("SELECT count(*) FROM measurement")[0] == 0


def test_body_fat_needs_sex_and_birth_date(unlocked):
    client_id = add_client(unlocked, first_name="No", last_name="Details", date_of_birth="", sex="")
    data = {"measured_on": "01/09/2026", "sf_triceps": "20", "sf_suprailiac": "15", "sf_thigh": "25", "body_fat_method": "jackson_pollock_3"}
    response = unlocked.post(f"/clients/{client_id}/measurements/new", data=data)
    assert response.status_code == 400 and "needs the client" in response.text


def test_edit_and_delete_measurement(unlocked, jane):
    url = f"/clients/{jane}/measurements"
    unlocked.post(f"{url}/new", data={"measured_on": "01/09/2026", "weight_kg": "72"})
    measurement_id = core_of(unlocked).db.one("SELECT id FROM measurement")["id"]
    assert 'value="72"' in unlocked.get(f"{url}/{measurement_id}/edit").text
    unlocked.post(f"{url}/{measurement_id}/edit", data={"measured_on": "02/09/2026", "weight_kg": "71.4"})
    assert core_of(unlocked).db.one("SELECT weight_kg FROM measurement")["weight_kg"] == 71.4
    other = add_client(unlocked, first_name="Omar", last_name="Khan")
    assert unlocked.post(f"/clients/{other}/measurements/{measurement_id}/delete").status_code == 404
    unlocked.post(f"{url}/{measurement_id}/delete")
    assert core_of(unlocked).db.one("SELECT count(*) FROM measurement")[0] == 0
