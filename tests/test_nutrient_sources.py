import pytest

from foodforthought import analysis, reference

from .test_clients import add_client
from .test_foods import imported, upload  # noqa: F401
from .test_recipes_plans import food, make_recipe


def test_sources_are_ranked_per_day_and_scaled():
    entries = [
        {"day": "2026-11-10", "grams": 200, "name": "Oats", "nutrients": {"energy_kcal": 100, "protein": 10, "sodium": 100}},
        {"day": "2026-11-10", "grams": 100, "name": "Milk", "nutrients": {"energy_kcal": 50, "protein": 4, "sodium": 40}},
        {"day": "2026-11-11", "grams": 100, "name": "Oats", "nutrients": {"energy_kcal": 100, "protein": 10, "sodium": None}},
    ]
    result = analysis.analyse(entries, [], reference.group_for("female", 30), None, None)
    macros = {m["key"]: m for m in result["macros"]}
    protein = macros["protein"]["sources"]
    assert [s["name"] for s in protein] == ["Oats", "Milk"]
    assert protein[0]["amount"] == pytest.approx(15) and protein[0]["share"] == pytest.approx(30 / 34)
    assert protein[1]["amount"] == pytest.approx(2)
    salt = macros["sodium"]["sources"]
    assert salt[0]["name"] == "Oats" and salt[0]["amount"] == pytest.approx(100 * 2.5 / 1000) and salt[0]["share"] == pytest.approx(200 / 240)
    micros = {m["key"]: m for m in result["micros"]}
    assert micros["calcium"]["sources"] == []


def test_long_lists_are_cut_short():
    amounts = {f"Food {n}": float(n) for n in range(1, 10)}
    rows = analysis.top_sources(amounts, 45.0)
    assert len(rows) == 7 and rows[0]["name"] == "Food 9" and rows[-1]["name"] == "3 other foods"
    assert rows[-1]["amount"] == pytest.approx(6) and sum(r["share"] for r in rows) == pytest.approx(1)
    assert analysis.top_sources({"Only": 0.0}, 0.0) == []


def test_pages_show_the_sources(imported):
    client_id = add_client(imported)
    recipe_id = make_recipe(imported)
    page = imported.get(f"/recipes/{recipe_id}").text
    tip = page.split('<span class="source-tip" tabindex="0">Protein')[1].split("</span></span></span>")[0]
    assert "Main sources" in tip and "Milk, semi-skimmed" in tip and "porridge oats" in tip and "3.5 g &middot; 61%" in tip

    plan_id = int(imported.post(f"/clients/{client_id}/meal-plans/new", data={"title": "W", "days": "1"}).headers["location"].rsplit("/", 1)[1])
    imported.post(f"/meal-plans/{plan_id}/items", data={"ref": f"recipe:{recipe_id}", "day": "1", "meal": "Breakfast", "servings": "2"})
    nutrition = imported.get(f"/meal-plans/{plan_id}/nutrition").text
    calcium = nutrition.split('tabindex="0">Calcium')[1].split("</span></span></span>")[0]
    assert "Milk, semi-skimmed, pasteurised, average" in calcium and "240 mg &middot; 92%" in calcium and "Porridge, made with water" in calcium
    assert "Point at a nutrient" in nutrition

    diary = imported.post(f"/clients/{client_id}/diaries/new", data={"title": "D", "start_date": "10/11/2026", "days": "1"}).headers["location"].rsplit("/", 1)[1]
    imported.post(f"/diaries/{diary}/entries", data={"day": "2026-11-10", "meal": "Lunch", "food_id": food(imported, "14-001"), "amount": "100"})
    analysis_page = imported.get(f"/diaries/{diary}/analysis").text
    assert 'tabindex="0">Energy<span class="tip"' in analysis_page and "Bread, wholemeal, average</span><span class=\"tip-n\">217 kcal &middot; 100%" in analysis_page
