import pytest

from foodforthought.matching import FoodIndex, normal_unit, remember, score, split_amount, text_key, tokens, weight

from .conftest import core_of
from .test_foods import imported, upload  # noqa: F401


def test_tokens_and_keys():
    assert tokens("Semi-skimmed MILK, pasteurised") == ["semi", "skimmed", "milk", "pasteurised"]
    assert tokens("a glass of the berries with tomatoes") == ["glass", "berry", "tomato"]
    assert text_key("semi skimmed milk") == text_key("Milk (semi-skimmed)") == text_key("milk, semi skimmed")


def test_score_prefers_close_names():
    milk = "Milk, semi-skimmed, pasteurised, average"
    assert score("semi skimmed milk", milk) > score("semi skimmed milk", "Milk chocolate")
    assert score("porridge", "Porridge, made with water") > score("porridge", "Bread, wholemeal, average")
    assert score("wholemeal bread", "Bread, wholemeal, average") > 70
    assert score("", milk) == 0


@pytest.mark.parametrize(
    "text, expected",
    [("1.00 cup", (1.0, "cup")), ("150.00 g", (150.0, "g")), ("1 1/2 tbsp", (1.5, "tbsp")), ("½ cup", (0.5, "cup")), ("a handful", (1, "handful")), ("", (None, ""))],
)
def test_split_amount(text, expected):
    assert split_amount(text) == expected


def test_weight_rules():
    food = {"nutrients": {"energy_kcal": 50}}
    portions = [{"name": "1 medium slice", "grams": 36}, {"name": "1 glass", "grams": 200}]
    assert weight(150, "g", food, portions).grams == 150
    assert weight(0.2, "kg", food, portions).grams == 200
    assert weight(2, "oz", food, portions).grams == 56.7
    assert weight(250, "ml", food, portions).how == "volume"
    assert weight(2, "slices", food, portions).grams == 72
    assert weight(1, "glasses", food, portions).grams == 200
    assert weight(1, "x 30g bar", food, portions).grams == 30
    estimated = weight(1, "cup", food, portions, app_kcal=100)
    assert estimated.grams == 200 and estimated.how == "energy"
    assert weight(1, "cup", food, portions).grams is None
    assert weight(None, "cup", food, portions).grams is None
    assert weight(2, "", food, portions).grams is None
    assert weight(1, "fl oz", food, portions).grams is None
    assert normal_unit("Slices (approx)") == "slice" and normal_unit("tbsp") == "tablespoon"


def test_index_suggests_and_remembers(imported):
    db = core_of(imported).db
    index = FoodIndex(db)
    first = index.suggest("semi skimmed milk")
    names = [index.names[s["food_id"]] for s in first]
    assert names[0] == "Milk, semi-skimmed, pasteurised, average"
    assert index.suggest("wholemeal bread")[0]["food_id"] == db.one("SELECT id FROM food WHERE code = '14-001'")["id"]
    assert index.suggest("") == []
    porridge = db.one("SELECT id FROM food WHERE code = '11-001'")["id"]
    remember(db, "Mum's oats", porridge, "2026-11-17T10:00:00Z")
    remember(db, "mums OATS", porridge, "2026-11-17T10:00:00Z")
    assert db.one("SELECT confirmed_count FROM food_match")["confirmed_count"] == 2
    again = FoodIndex(db).suggest("Mums oats")
    assert again[0] == {"food_id": porridge, "score": 100.0, "remembered": True}
