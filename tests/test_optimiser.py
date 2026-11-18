import pytest

from foodforthought import optimiser

from .conftest import core_of
from .test_clients import add_client
from .test_foods import imported, upload  # noqa: F401
from .test_recipes_plans import make_recipe, slice_portion

TARGET = {
    "set_on": "01/11/2026", "sex": "female", "age": "36", "weight_kg": "65", "height_cm": "165", "equation": "henry",
    "activity": "1.55", "adjustment_kcal": "0", "protein_basis": "per_kg", "protein_value": "1", "fat_percent": "35",
}


def test_solver_moves_each_food_the_right_way():
    supply = [[100, 20, 0], [100, 0, 25]]
    scales = optimiser.solve(supply, [200, 30, 20], [2, 1.5, 1], 0.5, 2.0, [False, False])
    assert scales[0] > 1.3 and scales[1] < 1 and all(0.5 <= s <= 2.0 for s in scales)
    assert optimiser.solve(supply, [200, 30, 20], [2, 1.5, 1], 0.5, 2.0, [False, False]) == scales
    held = optimiser.solve(supply, [200, 30, 20], [2, 1.5, 1], 0.5, 2.0, [True, False])
    assert held[0] == 1.0 and held[1] != 1.0
    assert optimiser.solve([[100]], [1000], [1], 0.75, 1.25, [False]) == [1.25]
    assert optimiser.solve([[0]], [1000], [1], 0.5, 2, [False]) == [1.0]
    assert optimiser.solve([[100]], [100], [1], 0.5, 2, [False]) == pytest.approx([1.0])


def test_rounding():
    assert optimiser.rounded({"kind": "food", "grams": 150, "quantity": None}, 1.234) == {"grams": 185, "factor": 185 / 150}
    assert optimiser.rounded({"kind": "food", "grams": 12, "quantity": None}, 1.3)["grams"] == 16
    assert optimiser.rounded({"kind": "food", "grams": 2, "quantity": None}, 0.1)["grams"] == 1
    sliced = optimiser.rounded({"kind": "food", "grams": 72, "quantity": 2, "unit": "1 slice"}, 1.3)
    assert sliced["quantity"] == 2.5 and sliced["grams"] == 90
    assert optimiser.rounded({"kind": "recipe", "servings": 1}, 1.37)["servings"] == 1.25
    assert optimiser.rounded({"kind": "recipe", "servings": 1}, 0.01)["servings"] == 0.25


def test_suggest_and_apply(imported):
    db = core_of(imported).db
    client_id = add_client(imported)
    recipe_id = make_recipe(imported)
    bread, portion = slice_portion(imported)
    plan_id = int(imported.post(f"/clients/{client_id}/meal-plans/new", data={"title": "W", "days": "2"}).headers["location"].rsplit("/", 1)[1])
    imported.post(f"/meal-plans/{plan_id}/items", data={"ref": f"recipe:{recipe_id}", "day": "1", "meal": "Breakfast", "servings": "1"})
    imported.post(f"/meal-plans/{plan_id}/items", data={"ref": f"food:{bread}", "day": "1", "meal": "Lunch", "amount": "2", "unit": f"portion:{portion}"})
    url = f"/meal-plans/{plan_id}/days/1/portions"
    assert "Set targets for this client first" in imported.get(url).text
    assert "Suggest portions" not in imported.get(f"/meal-plans/{plan_id}").text

    imported.post(f"/clients/{client_id}/targets/new", data=TARGET)
    assert "Suggest portions" in imported.get(f"/meal-plans/{plan_id}").text
    page = imported.get(url).text
    assert "Energy: 2090 kcal" in page and "271 kcal (13%)" in page and "1.5 servings" in page and "3 × slice (108 g)" in page
    assert "Nothing is planned" in imported.get(f"/meal-plans/{plan_id}/days/2/portions").text
    assert imported.get(f"/meal-plans/{plan_id}/days/3/portions").status_code == 404
    doubled = imported.get(url + "?room=100").text
    assert "2 servings" in doubled and "4 × slice (144 g)" in doubled

    bread_item = db.one("SELECT id FROM meal_plan_item WHERE food_id = ?", (bread,))["id"]
    recipe_item = db.one("SELECT id FROM meal_plan_item WHERE recipe_id = ?", (recipe_id,))["id"]
    kept = imported.get(url + f"?room=100&keep={bread_item}").text
    assert "kept" in kept and "4 × slice" not in kept

    done = imported.post(url, data={"room": "100", "keep": str(bread_item), "apply": [str(recipe_item), str(bread_item)]})
    assert done.headers["location"].endswith("msg=portions-applied#day-1")
    assert db.one("SELECT servings FROM meal_plan_item WHERE id = ?", (recipe_item,))["servings"] == 2
    assert tuple(db.one("SELECT grams, quantity FROM meal_plan_item WHERE id = ?", (bread_item,))) == (72, 2)
    imported.post(url, data={"room": "50", "apply": [str(bread_item)]})
    assert tuple(db.one("SELECT grams, quantity FROM meal_plan_item WHERE id = ?", (bread_item,))) == (108, 3)
    assert db.one("SELECT servings FROM meal_plan_item WHERE id = ?", (recipe_item,))["servings"] == 2
    assert db.one("SELECT count(*) FROM audit_log WHERE action = 'meal_plan_portions'")[0] == 2

    template = int(imported.post(f"/meal-plans/{plan_id}/save-template").headers["location"].split("/")[2].split("?")[0])
    assert "Templates have no targets" in imported.get(f"/meal-plans/{template}/days/1/portions").text
