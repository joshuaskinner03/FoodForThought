import io
import json
import zipfile

import pytest
from pypdf import PdfReader

from foodforthought import meal_plans, recipes

from .conftest import core_of
from .test_clients import add_client
from .test_foods import imported, upload  # noqa: F401

HX = {"HX-Request": "true"}


def db(client):
    return core_of(client).db


def food(client, code):
    return db(client).one("SELECT id FROM food WHERE code = ?", (code,))["id"]


def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def make_recipe(client, name="Porridge with milk", servings="2"):
    done = client.post("/recipes/new", data={"name": name, "servings": servings})
    recipe_id = int(done.headers["location"].rsplit("/", 1)[1])
    client.post(f"/recipes/{recipe_id}/items", data={"food_id": food(client, "11-001"), "amount": "300", "unit": "g", "label": "porridge oats"})
    client.post(f"/recipes/{recipe_id}/items", data={"food_id": food(client, "12-001"), "amount": "200", "unit": "g"})
    return recipe_id


def slice_portion(client):
    bread = food(client, "14-001")
    client.post(f"/foods/{bread}/portions", data={"name": "1 slice", "grams": "36"})
    return bread, db(client).one("SELECT id FROM portion WHERE food_id = ?", (bread,))["id"]


def test_recipe_maths():
    rows = [
        {"grams": 300, "nutrients": {"energy_kcal": 46, "protein": 1.5, "calcium": 7, "iodine": None}},
        {"grams": 200, "nutrients": json.dumps({"energy_kcal": 46, "protein": 3.5, "calcium": 120, "iodine": 30})},
    ]
    found = recipes.totals(rows)
    assert found["energy_kcal"] == {"value": pytest.approx(230), "gap": False}
    assert found["calcium"]["value"] == pytest.approx(261) and found["iodine"] == {"value": pytest.approx(60), "gap": True}
    assert found["vitamin_c"] == {"value": 0, "gap": True}
    assert recipes.scale(found, 0.5)["protein"]["value"] == pytest.approx(5.75)
    assert recipes.amount_text(72, 2, "1 slice") == "2 × slice (72 g)"
    assert recipes.amount_text(150, None, "g") == "150 g" and recipes.amount_text(2.5, None, "g") == "2.5 g"
    assert meal_plans.servings_text(1) == "1 serving" and meal_plans.servings_text(1.5) == "1.5 servings"


def test_recipe_library(imported):
    recipe_id = make_recipe(imported)
    page = imported.get(f"/recipes/{recipe_id}").text
    assert "porridge oats" in page and "(Porridge, made with water)" in page and "300 g" in page
    assert "Serves 2 &middot; 250 g a serving" in page and "115 kcal" in page and "5.8 g" in page
    assert "Total weight of ingredients 500 g" in page
    choices = imported.get(f"/recipes/{recipe_id}/food-choices?q=bread", headers=HX).text
    assert "Bread, wholemeal" in choices and "<html" not in choices and 'data-q="bread"' in choices
    bread, portion = slice_portion(imported)
    form = imported.get(f"/recipes/{recipe_id}/item-form?ref=food:{bread}", headers=HX).text
    assert "1 slice (36 g)" in form and 'name="food_id"' in form
    panel = imported.post(f"/recipes/{recipe_id}/items", data={"food_id": bread, "amount": "2", "unit": f"portion:{portion}"}, headers=HX)
    assert panel.status_code == 200 and 'id="recipe-items"' in panel.text and "2 × slice (72 g)" in panel.text
    bad = imported.post(f"/recipes/{recipe_id}/items", data={"food_id": bread, "amount": "0", "unit": "g"}, headers=HX)
    assert bad.status_code == 400 and "Enter an amount above 0." in bad.text
    item = db(imported).one("SELECT id FROM recipe_item WHERE food_id = ?", (bread,))["id"]
    imported.post(f"/recipes/{recipe_id}/items/{item}/delete", headers=HX)

    saved = imported.post(f"/recipes/{recipe_id}", data={"name": "Creamy porridge", "servings": "4", "cooked_grams": "600", "tags": "breakfast,  vegetarian", "method": "Heat gently.\n\nStir."})
    assert saved.status_code == 303
    row = db(imported).one("SELECT * FROM recipe WHERE id = ?", (recipe_id,))
    assert (row["name"], row["servings"], row["cooked_grams"], row["tags"]) == ("Creamy porridge", 4, 600, "breakfast, vegetarian")
    assert "150 g a serving" in imported.get(f"/recipes/{recipe_id}").text
    bad = imported.post(f"/recipes/{recipe_id}", data={"name": "", "servings": "0"})
    assert bad.status_code == 400 and "Enter a name" in bad.text and "how many servings" in bad.text

    listing = imported.get("/recipes?q=vegetarian").text
    assert "Creamy porridge" in listing and ">58<" in listing
    assert "Creamy porridge" not in imported.get("/recipes?q=curry").text

    copy = imported.post(f"/recipes/{recipe_id}/duplicate").headers["location"]
    copy_id = int(copy.split("/")[2].split("?")[0])
    assert db(imported).one("SELECT count(*) FROM recipe_item WHERE recipe_id = ?", (copy_id,))[0] == 2
    imported.post(f"/recipes/{copy_id}/archive")
    assert "(copy)" not in imported.get("/recipes").text and "(copy)" in imported.get("/recipes?archived=1").text
    assert imported.post(f"/recipes/{copy_id}/delete").headers["location"] == "/recipes?msg=deleted"
    assert imported.get("/recipes/9999").headers["location"] == "/recipes?err=missing"


@pytest.fixture
def plan(imported):
    client_id = add_client(imported)
    done = imported.post(f"/clients/{client_id}/meal-plans/new", data={"title": "Week 1", "days": "3", "start_date": "24/11/2026"})
    plan_id = int(done.headers["location"].rsplit("/", 1)[1])
    return {"client": client_id, "plan": plan_id, "recipe": make_recipe(imported)}


def test_building_a_plan(imported, plan):
    plan_id, recipe_id = plan["plan"], plan["recipe"]
    bread, portion = slice_portion(imported)
    page = imported.get(f"/meal-plans/{plan_id}").text
    assert "Day 1, Tuesday 24 November 2026" in page and "Day 3, Thursday 26 November 2026" in page and "Nothing planned yet" in page

    found = imported.get(f"/meal-plans/{plan_id}/choices?q=porridge&day=2&meal=Lunch", headers=HX).text
    assert found.index("Porridge with milk") < found.index("Porridge, made with water") and "115 kcal/serving" in found
    assert f"?ref=recipe:{recipe_id}&amp;day=2&amp;meal=Lunch" in found
    form = imported.get(f"/meal-plans/{plan_id}/item-form?ref=recipe:{recipe_id}&day=2&meal=Lunch", headers=HX).text
    assert 'name="servings"' in form and "115 kcal a serving" in form and '<option value="2" selected>' in form

    panel = imported.post(f"/meal-plans/{plan_id}/items", data={"ref": f"recipe:{recipe_id}", "day": "1", "meal": "Breakfast", "servings": "1"}, headers=HX)
    assert panel.status_code == 200 and 'id="plan-panel"' in panel.text and "1 serving &middot; 115 kcal" in panel.text
    imported.post(f"/meal-plans/{plan_id}/items", data={"ref": f"food:{bread}", "day": "1", "meal": "Lunch", "amount": "2", "unit": f"portion:{portion}", "label": "Wholemeal toast"})
    page = imported.get(f"/meal-plans/{plan_id}").text
    assert "Wholemeal toast" in page and "2 × slice (72 g) &middot; 156 kcal" in page and "271 kcal" in page
    for bad in ({"ref": f"food:{bread}", "day": "9", "meal": "Lunch", "amount": "1"}, {"ref": "", "day": "1", "meal": "Lunch"}, {"ref": f"recipe:{recipe_id}", "day": "1", "meal": "Lunch", "servings": "0"}):
        assert imported.post(f"/meal-plans/{plan_id}/items", data=bad, headers=HX).status_code == 400

    imported.post(f"/clients/{plan['client']}/targets/new", data={
        "set_on": "01/11/2026", "sex": "female", "age": "36", "weight_kg": "65", "height_cm": "165", "equation": "henry",
        "activity": "1.55", "adjustment_kcal": "0", "protein_basis": "per_kg", "protein_value": "1", "fat_percent": "35",
    })
    page = imported.get(f"/meal-plans/{plan_id}").text
    assert "% of target)" in page and "<td>Target</td>" in page

    done = imported.post(f"/meal-plans/{plan_id}/copy-day", data={"from": "1", "to": "2", "replace": "on"})
    assert done.headers["location"].endswith("msg=day-copied#day-2")
    imported.post(f"/meal-plans/{plan_id}/copy-day", data={"from": "1", "to": "2"})
    assert db(imported).one("SELECT count(*) FROM meal_plan_item WHERE plan_id = ? AND day = 2", (plan_id,))[0] == 4
    imported.post(f"/meal-plans/{plan_id}/copy-day", data={"from": "1", "to": "2", "replace": "on"})
    assert db(imported).one("SELECT count(*) FROM meal_plan_item WHERE plan_id = ? AND day = 2", (plan_id,))[0] == 2
    assert imported.post(f"/meal-plans/{plan_id}/copy-day", data={"from": "1", "to": "1"}).headers["location"].endswith("err=copy-day")
    item = db(imported).one("SELECT id FROM meal_plan_item WHERE plan_id = ? AND day = 2 AND food_id IS NOT NULL", (plan_id,))["id"]
    assert imported.post(f"/meal-plans/{plan_id}/items/{item}/delete", headers=HX).status_code == 200

    shopping = imported.get(f"/meal-plans/{plan_id}/shopping").text
    assert shopping.index("Cereals and cereal products") < shopping.index("Milk and milk products")
    assert "Porridge, made with water" in shopping and "300 g" in shopping
    assert "Bread, wholemeal, average" in shopping and "70 g (about 2 × slice)" in shopping and "200 g" in shopping

    nutrition = imported.get(f"/meal-plans/{plan_id}/nutrition").text
    assert "2 of 3 days have food planned" in nutrition and "Female, aged 36" in nutrition
    assert "what the plan provides" in nutrition and "Day 3, Thursday 26 November 2026" in nutrition

    assert imported.post(f"/recipes/{recipe_id}/delete").headers["location"].endswith("err=recipe-used")


def test_plan_settings_templates_and_pdf(imported, plan):
    plan_id, recipe_id, client_id = plan["plan"], plan["recipe"], plan["client"]
    for day in ("1", "3"):
        imported.post(f"/meal-plans/{plan_id}/items", data={"ref": f"recipe:{recipe_id}", "day": day, "meal": "Breakfast", "servings": "2"})
    imported.post(f"/recipes/{recipe_id}", data={"name": "Porridge with milk", "servings": "2", "method": "Heat the oats and milk together, stirring."})
    saved = imported.post(f"/meal-plans/{plan_id}/settings", data={"title": "Week one", "days": "2", "start_date": "", "intro": "Hello Jane.\n\nEnjoy."})
    assert saved.status_code == 303
    row = db(imported).one("SELECT * FROM meal_plan WHERE id = ?", (plan_id,))
    assert (row["title"], row["days"], row["start_date"]) == ("Week one", 2, None)
    assert db(imported).one("SELECT count(*) FROM meal_plan_item WHERE plan_id = ?", (plan_id,))[0] == 1
    assert imported.post(f"/meal-plans/{plan_id}/settings", data={"title": "x", "days": "20"}).status_code == 400

    response = imported.get(f"/meal-plans/{plan_id}/pdf?recipes=1&shopping=1")
    assert response.headers["content-type"] == "application/pdf"
    text = pdf_text(response.content)
    assert "Week one" in text and "Prepared for Jane Smith" in text and "Hello Jane." in text
    assert "Day 1" in text and "Porridge with milk: 2 servings" in text and "Nothing planned for this day." in text
    assert "Recipes" in text and "porridge oats: 300 g" in text and "Heat the oats" in text
    assert "Shopping list" in text and "kcal" not in text
    with_figures = pdf_text(imported.get(f"/meal-plans/{plan_id}/pdf?figures=1").content)
    assert "230 kcal" in with_figures and "Day total: 230 kcal" in with_figures and "Shopping list" not in with_figures
    stored = imported.post(f"/meal-plans/{plan_id}/pdf/save", data={"recipes": "1"})
    assert stored.headers["location"].endswith("msg=pdf-saved")
    attachment = db(imported).one("SELECT * FROM attachment WHERE client_id = ?", (client_id,))
    assert attachment["filename"] == "Meal plan - Week one.pdf"

    template = imported.post(f"/meal-plans/{plan_id}/save-template").headers["location"]
    template_id = int(template.split("/")[2].split("?")[0])
    row = db(imported).one("SELECT * FROM meal_plan WHERE id = ?", (template_id,))
    assert row["client_id"] is None and row["title"] == "Week one"
    page = imported.get(f"/meal-plans/{template_id}").text
    assert "Meal plan template" in page and "Save to client" not in page
    assert "Week one" in imported.get("/plan-templates").text
    assert "No client, so no reference values" in imported.get(f"/meal-plans/{template_id}/nutrition").text

    other = add_client(imported, first_name="Omar", last_name="Khan")
    assert "Template: Week one (2 days)" in imported.get(f"/clients/{other}/meal-plans").text
    made = imported.post(f"/clients/{other}/meal-plans/new", data={"title": "", "template": str(template_id), "start_date": "01/12/2026"})
    new_id = int(made.headers["location"].rsplit("/", 1)[1])
    row = db(imported).one("SELECT * FROM meal_plan WHERE id = ?", (new_id,))
    assert (row["client_id"], row["title"], row["start_date"]) == (other, "Week one", "2026-12-01")
    assert db(imported).one("SELECT count(*) FROM meal_plan_item WHERE plan_id = ?", (new_id,))[0] == 1
    assert "Template: Week one" not in imported.get(f"/meal-plans/{new_id}").text

    done = imported.post("/plan-templates/new", data={"title": "Vegetarian", "days": "7"})
    assert done.status_code == 303 and imported.post("/plan-templates/new", data={"title": "", "days": "7"}).headers["location"].endswith("err=plan-days")
    assert imported.post(f"/clients/{other}/meal-plans/new", data={"title": "x", "days": "0"}).headers["location"].endswith("err=plan-days")

    imported.post(f"/meal-plans/{new_id}/delete")
    assert db(imported).one("SELECT count(*) FROM meal_plan WHERE id = ?", (new_id,))[0] == 0
    assert imported.get("/meal-plans/9999").status_code == 404


def test_export_and_erase(imported, plan):
    plan_id, recipe_id, client_id = plan["plan"], plan["recipe"], plan["client"]
    imported.post(f"/meal-plans/{plan_id}/items", data={"ref": f"recipe:{recipe_id}", "day": "1", "meal": "Breakfast", "servings": "1", "label": "Jane's porridge"})
    archive = zipfile.ZipFile(io.BytesIO(imported.post(f"/clients/{client_id}/export").content))
    record = json.loads(archive.read("record.json"))
    assert record["meal_plans"][0]["title"] == "Week 1" and record["meal_plans"][0]["items"][0]["recipe"] == "Porridge with milk"
    assert "Meal plans" in archive.read("record.html").decode()
    assert "meal plans: 1" in imported.get(f"/clients/{client_id}/data").text
    imported.post(f"/clients/{client_id}/erase", data={"confirm": "Jane Smith"})
    assert db(imported).one("SELECT count(*) FROM meal_plan")[0] == 0 and db(imported).one("SELECT count(*) FROM meal_plan_item")[0] == 0
    assert db(imported).one("SELECT count(*) FROM recipe")[0] == 1


def test_pages_need_unlocking(client):
    for path in ("/recipes", "/plan-templates", "/meal-plans/1"):
        assert client.get(path).status_code in (303, 307)
