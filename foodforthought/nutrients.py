from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Nutrient:
    key: str
    code: str
    label: str
    unit: str
    group: str
    places: int = 1
    main: bool = False


NUTRIENTS = (
    Nutrient("energy_kcal", "KCALS", "Energy", "kcal", "Energy and macronutrients", 0, True),
    Nutrient("energy_kj", "KJ", "Energy", "kJ", "Energy and macronutrients", 0),
    Nutrient("protein", "PROT", "Protein", "g", "Energy and macronutrients", 1, True),
    Nutrient("fat", "FAT", "Fat", "g", "Energy and macronutrients", 1, True),
    Nutrient("saturates", "SATFOD", "of which saturates", "g", "Energy and macronutrients", 1, True),
    Nutrient("monounsaturates", "MONOFOD", "of which monounsaturates", "g", "Energy and macronutrients"),
    Nutrient("polyunsaturates", "POLYFOD", "of which polyunsaturates", "g", "Energy and macronutrients"),
    Nutrient("trans", "FODTRANS", "of which trans", "g", "Energy and macronutrients", 2),
    Nutrient("carbohydrate", "CHO", "Carbohydrate", "g", "Energy and macronutrients", 1, True),
    Nutrient("sugars", "TOTSUG", "of which total sugars", "g", "Energy and macronutrients", 1, True),
    Nutrient("starch", "STAR", "of which starch", "g", "Energy and macronutrients"),
    Nutrient("fibre", "AOACFIB", "Fibre (AOAC)", "g", "Energy and macronutrients", 1, True),
    Nutrient("fibre_nsp", "ENGFIB", "Fibre (NSP)", "g", "Energy and macronutrients"),
    Nutrient("alcohol", "ALCO", "Alcohol", "g", "Energy and macronutrients"),
    Nutrient("cholesterol", "CHOL", "Cholesterol", "mg", "Energy and macronutrients", 0),
    Nutrient("water", "WATER", "Water", "g", "Energy and macronutrients"),
    Nutrient("sodium", "NA", "Sodium", "mg", "Minerals", 0, True),
    Nutrient("potassium", "K", "Potassium", "mg", "Minerals", 0),
    Nutrient("calcium", "CA", "Calcium", "mg", "Minerals", 0),
    Nutrient("magnesium", "MG", "Magnesium", "mg", "Minerals", 0),
    Nutrient("phosphorus", "P", "Phosphorus", "mg", "Minerals", 0),
    Nutrient("iron", "FE", "Iron", "mg", "Minerals", 2),
    Nutrient("copper", "CU", "Copper", "mg", "Minerals", 2),
    Nutrient("zinc", "ZN", "Zinc", "mg", "Minerals", 1),
    Nutrient("chloride", "CL", "Chloride", "mg", "Minerals", 0),
    Nutrient("manganese", "MN", "Manganese", "mg", "Minerals", 2),
    Nutrient("selenium", "SE", "Selenium", "µg", "Minerals", 0),
    Nutrient("iodine", "I", "Iodine", "µg", "Minerals", 0),
    Nutrient("vitamin_a", "RETEQU", "Vitamin A (retinol equivalents)", "µg", "Vitamins", 0),
    Nutrient("retinol", "RET", "Retinol", "µg", "Vitamins", 0),
    Nutrient("carotene", "CAREQU", "Carotene", "µg", "Vitamins", 0),
    Nutrient("vitamin_d", "VITD", "Vitamin D", "µg", "Vitamins", 1),
    Nutrient("vitamin_e", "VITE", "Vitamin E", "mg", "Vitamins", 2),
    Nutrient("vitamin_k1", "VITK1", "Vitamin K1", "µg", "Vitamins", 1),
    Nutrient("thiamin", "THIA", "Thiamin", "mg", "Vitamins", 2),
    Nutrient("riboflavin", "RIBO", "Riboflavin", "mg", "Vitamins", 2),
    Nutrient("niacin_equivalent", "NIACEQU", "Niacin equivalents", "mg", "Vitamins", 1),
    Nutrient("niacin", "NIAC", "Niacin", "mg", "Vitamins", 1),
    Nutrient("tryptophan60", "TRYP60", "Tryptophan / 60", "mg", "Vitamins", 1),
    Nutrient("vitamin_b6", "VITB6", "Vitamin B6", "mg", "Vitamins", 2),
    Nutrient("vitamin_b12", "VITB12", "Vitamin B12", "µg", "Vitamins", 1),
    Nutrient("folate", "FOLT", "Folate", "µg", "Vitamins", 0),
    Nutrient("pantothenate", "PANTO", "Pantothenate", "mg", "Vitamins", 2),
    Nutrient("biotin", "BIOT", "Biotin", "µg", "Vitamins", 1),
    Nutrient("vitamin_c", "VITC", "Vitamin C", "mg", "Vitamins", 0),
)
BY_KEY = {n.key: n for n in NUTRIENTS}
BY_CODE = {n.code: n for n in NUTRIENTS}
GROUPS = tuple(dict.fromkeys(n.group for n in NUTRIENTS))
KCAL_PER_GRAM = {"protein": 4.0, "fat": 9.0, "carbohydrate": 3.75, "alcohol": 7.0}

FOOD_GROUPS = {
    "A": "Cereals and cereal products",
    "B": "Milk and milk products",
    "C": "Eggs",
    "D": "Vegetables",
    "F": "Fruit",
    "G": "Nuts and seeds",
    "H": "Herbs and spices",
    "J": "Fish and fish products",
    "M": "Meat and meat products",
    "O": "Fats and oils",
    "P": "Beverages",
    "Q": "Alcoholic beverages",
    "S": "Sugars, preserves and snacks",
    "W": "Soups, sauces and miscellaneous foods",
}


def food_group_name(code: str) -> str:
    return FOOD_GROUPS.get(code[:1].upper(), "") if code else ""


def energy_from_macros(values: dict) -> float | None:
    if any(values.get(key) is None for key in ("protein", "fat", "carbohydrate")):
        return None
    return sum((values.get(key) or 0) * factor for key, factor in KCAL_PER_GRAM.items())
