from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fractions import Fraction

from rapidfuzz import fuzz, process

from .db import Database

STOP_WORDS = {"a", "an", "the", "of", "with", "and", "in", "on", "my", "some", "made", "x"}
WEIGHT_UNITS = {"g": 1, "gram": 1, "grams": 1, "gr": 1, "kg": 1000, "kilogram": 1000, "oz": 28.35, "ounce": 28.35, "ounces": 28.35, "lb": 453.6, "lbs": 453.6}
VOLUME_UNITS = {"ml": 1, "millilitre": 1, "milliliter": 1, "millilitres": 1, "milliliters": 1, "l": 1000, "litre": 1000, "liter": 1000, "litres": 1000}
UNIT_WORDS = {
    "tsp": "teaspoon", "teaspoons": "teaspoon", "tbsp": "tablespoon", "tablespoons": "tablespoon", "tbs": "tablespoon",
    "slices": "slice", "cups": "cup", "glasses": "glass", "mugs": "mug", "bowls": "bowl", "pieces": "piece",
    "pcs": "piece", "packs": "pack", "packets": "pack", "packet": "pack", "cans": "can", "tins": "can", "tin": "can",
    "bottles": "bottle", "portions": "portion", "servings": "serving", "handfuls": "handful", "biscuits": "biscuit",
}
FRACTIONS = {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3"}
WORD_NUMBERS = {"half": 0.5, "quarter": 0.25, "one": 1, "two": 2, "three": 3, "four": 4, "a": 1, "an": 1}
_AMOUNT = re.compile(r"^\s*(\d+\s+\d+/\d+|\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?)?\s*(.*?)\s*$")
_GRAMS_IN = re.compile(r"\(?\s*(\d+(?:\.\d+)?)\s*(g|ml)\b", re.IGNORECASE)


def _stem(word: str) -> str:
    if len(word) <= 3:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("oes", "sses", "ches", "shes", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    words = re.sub(r"[^a-z0-9]+", " ", re.sub(r"['’`]", "", (text or "").lower())).split()
    return [_stem(word) for word in words if word not in STOP_WORDS and (len(word) > 1 or word.isdigit())]


def text_key(text: str) -> str:
    return " ".join(sorted(set(tokens(text))))


def _coverage(query: list[str], name: list[str]) -> tuple[float, float]:
    if not query or not name:
        return 0.0, 1.0
    hit = [any(part == word or (len(word) >= 3 and part.startswith(word)) for part in name) for word in query]
    used = [any(part == word or (len(word) >= 3 and part.startswith(word)) for word in query) for part in name]
    return sum(hit) / len(query), 1 - sum(used) / len(name)


def score(query: str, name: str) -> float:
    q, n = tokens(query), tokens(name)
    if not q:
        return 0.0
    coverage, extra = _coverage(q, n)
    joined_q, joined_n = " ".join(q), " ".join(n)
    combined = (
        0.45 * coverage
        + 0.25 * fuzz.token_set_ratio(joined_q, joined_n) / 100
        + 0.20 * fuzz.token_sort_ratio(joined_q, joined_n) / 100
        + 0.10 * (1 - extra)
    )
    return round(combined * 100, 1)


class FoodIndex:
    def __init__(self, db: Database):
        rows = db.query(
            "SELECT f.id, f.name, s.key AS source_key FROM food f JOIN food_source s ON s.id = f.source_id "
            "WHERE f.archived = 0"
        )
        self.ids = [row["id"] for row in rows]
        self.names = {row["id"]: row["name"] for row in rows}
        self.custom = {row["id"] for row in rows if row["source_key"] == "custom"}
        self.choices = {row["id"]: " ".join(tokens(row["name"])) for row in rows}
        self.remembered = {row["text_key"]: row["food_id"] for row in db.query("SELECT text_key, food_id FROM food_match")}

    def suggest(self, text: str, limit: int = 5) -> list[dict]:
        query = " ".join(tokens(text))
        if not query or not self.choices:
            return []
        found = {
            food_id
            for _, _, food_id in process.extract(query, self.choices, scorer=fuzz.WRatio, limit=40)
        }
        words = query.split()
        for food_id, choice in self.choices.items():
            parts = choice.split()
            if all(any(part.startswith(word) for part in parts) for word in words):
                found.add(food_id)
        ranked = sorted(
            ((score(text, self.names[food_id]) + (2 if food_id in self.custom else 0), food_id) for food_id in found),
            key=lambda item: (-item[0], len(self.names[item[1]]), self.names[item[1]]),
        )
        suggestions = [{"food_id": food_id, "score": min(value, 100.0)} for value, food_id in ranked[:limit]]
        remembered = self.remembered.get(text_key(text))
        if remembered in self.names:
            suggestions = [s for s in suggestions if s["food_id"] != remembered]
            suggestions.insert(0, {"food_id": remembered, "score": 100.0, "remembered": True})
            suggestions = suggestions[:limit]
        return suggestions


def remember(db: Database, text: str, food_id: int, now: str) -> None:
    key = text_key(text)
    if not key:
        return
    db.execute(
        "INSERT INTO food_match(text_key, food_id, confirmed_count, updated_at) VALUES(?, ?, 1, ?) "
        "ON CONFLICT(text_key) DO UPDATE SET food_id = excluded.food_id, "
        "confirmed_count = food_match.confirmed_count + 1, updated_at = excluded.updated_at",
        (key, food_id, now),
    )


def parse_quantity(text) -> float | None:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else None
    cleaned = str(text).strip().lower()
    for symbol, replacement in FRACTIONS.items():
        cleaned = cleaned.replace(symbol, f" {replacement}")
    cleaned = cleaned.strip()
    if cleaned in WORD_NUMBERS:
        return WORD_NUMBERS[cleaned]
    try:
        parts = cleaned.replace(",", ".").split()
        total = sum(Fraction(part) for part in parts)
    except (ValueError, ZeroDivisionError):
        return None
    return float(total) if total > 0 else None


def split_amount(text: str) -> tuple[float | None, str]:
    cleaned = (text or "").strip()
    for symbol, replacement in FRACTIONS.items():
        cleaned = cleaned.replace(symbol, f" {replacement} ")
    match = _AMOUNT.match(cleaned)
    number, unit = (match[1], match[2]) if match else (None, cleaned)
    if not number:
        first, _, rest = cleaned.partition(" ")
        if first.lower() in WORD_NUMBERS:
            return WORD_NUMBERS[first.lower()], rest.strip()
        return None, cleaned
    return parse_quantity(number), unit.strip()


def normal_unit(unit: str) -> str:
    word = re.sub(r"\(.*?\)", "", (unit or "").lower()).strip().rstrip(".")
    word = word.split()[-1] if word.split() else ""
    return UNIT_WORDS.get(word, word)


@dataclass
class Weight:
    grams: float | None
    how: str
    note: str = ""


def weight(quantity: float | None, unit: str, food: dict | None, portions: list, app_kcal: float | None = None) -> Weight:
    if quantity is None:
        return Weight(None, "unknown", "The amount could not be read. Enter the weight.")
    raw = (unit or "").lower().strip().rstrip(".")
    first = raw.split()[0] if raw.split() else ""
    if not raw:
        return Weight(None, "unknown", "No unit was given. Enter the weight.")
    if first in WEIGHT_UNITS:
        factor = WEIGHT_UNITS.get(first, 1)
        return Weight(round(quantity * factor, 1), "weight")
    if first in VOLUME_UNITS or raw == "fl oz":
        if raw == "fl oz":
            return Weight(None, "unknown", "Fluid ounces differ between the UK and the US. Enter the weight.")
        return Weight(round(quantity * VOLUME_UNITS[first], 1), "volume", "Millilitres are counted as grams.")
    inside = _GRAMS_IN.search(raw)
    if inside:
        return Weight(round(quantity * float(inside[1]), 1), "label", f"From the {inside[1]} {inside[2]} given with the amount.")
    unit_word = normal_unit(unit)
    if unit_word:
        for portion in portions:
            portion_words = {UNIT_WORDS.get(word, word) for word in re.sub(r"[^a-z ]", " ", portion["name"].lower()).split()}
            if unit_word in portion_words:
                return Weight(round(quantity * portion["grams"], 1), "portion", f"From the saved portion \"{portion['name']}\".")
    if app_kcal and food and (food["nutrients"].get("energy_kcal") or 0) > 0:
        grams = app_kcal / food["nutrients"]["energy_kcal"] * 100
        return Weight(round(grams, 1), "energy", "Estimated from the energy the app recorded. Check it.")
    return Weight(None, "unknown", f"No saved portion for \"{unit_word or unit}\". Enter the weight.")


def dumps(suggestions: list[dict]) -> str:
    return json.dumps(suggestions)
