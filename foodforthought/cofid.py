from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from openpyxl import load_workbook

from .nutrients import BY_CODE, NUTRIENTS, energy_from_macros

ATTRIBUTION = (
    "McCance and Widdowson's The Composition of Foods Integrated Dataset 2021, Public Health England. "
    "Contains public sector information licensed under the Open Government Licence v3.0."
)
SHEETS = ("proximates", "inorganics", "vitamins")
HEADER_SCAN_ROWS = 12
IDENTITY = {"food code": "code", "food name": "name", "description": "description", "group": "group"}
_NUMBER = re.compile(r"^[<~]?\s*(-?\d+(?:\.\d+)?)$")


LABELS = tuple(
    (re.compile(pattern), key)
    for pattern, key in (
        (r"^energy \(kcal\)", "energy_kcal"),
        (r"^energy \(kj\)", "energy_kj"),
        (r"^protein", "protein"),
        (r"^fat \(", "fat"),
        (r"^carbohydrate", "carbohydrate"),
        (r"^starch", "starch"),
        (r"^total sugars", "sugars"),
        (r"^alcohol", "alcohol"),
        (r"^nsp", "fibre_nsp"),
        (r"^aoac", "fibre"),
        (r"^satd fas? ?/ ?100g f(oo)?d", "saturates"),
        (r"^mono fas? ?/ ?100g f(oo)?d", "monounsaturates"),
        (r"^poly fas? ?/ ?100g f(oo)?d", "polyunsaturates"),
        (r"^trans fas? ?/ ?100g f(oo)?d", "trans"),
        (r"^cholesterol", "cholesterol"),
        (r"^water", "water"),
        (r"^sodium", "sodium"),
        (r"^potassium", "potassium"),
        (r"^calcium", "calcium"),
        (r"^magnesium", "magnesium"),
        (r"^phosphorus", "phosphorus"),
        (r"^iron", "iron"),
        (r"^copper", "copper"),
        (r"^zinc", "zinc"),
        (r"^chloride", "chloride"),
        (r"^manganese", "manganese"),
        (r"^selenium", "selenium"),
        (r"^iodine", "iodine"),
        (r"^retinol equivalent", "vitamin_a"),
        (r"^retinol", "retinol"),
        (r"^carotene", "carotene"),
        (r"^vitamin d", "vitamin_d"),
        (r"^vitamin e", "vitamin_e"),
        (r"^vitamin k1", "vitamin_k1"),
        (r"^thiamin", "thiamin"),
        (r"^riboflavin", "riboflavin"),
        (r"^niacin equivalent", "niacin_equivalent"),
        (r"^niacin", "niacin"),
        (r"^tryptophan", "tryptophan60"),
        (r"^vitamin b6", "vitamin_b6"),
        (r"^vitamin b12", "vitamin_b12"),
        (r"^folate", "folate"),
        (r"^pantothenate", "pantothenate"),
        (r"^biotin", "biotin"),
        (r"^vitamin c", "vitamin_c"),
    )
)


def _by_label(cell) -> str | None:
    text = _norm(cell).lower()
    return next((key for pattern, key in LABELS if pattern.match(text)), None) if text else None


def _by_code(cell) -> str | None:
    found = BY_CODE.get(_norm(cell).upper())
    return found.key if found else None


class ImportProblem(Exception):
    pass


@dataclass
class ParsedFood:
    code: str
    name: str
    description: str = ""
    group: str = ""
    nutrients: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)


def parse_value(cell) -> tuple[float | None, str]:
    if cell is None:
        return None, "blank"
    if isinstance(cell, bool):
        return None, "unknown"
    if isinstance(cell, (int, float)):
        return float(cell), ""
    text = str(cell).strip()
    if not text:
        return None, "blank"
    lowered = text.lower()
    if lowered in ("tr", "trace"):
        return 0.0, "trace"
    if lowered == "n":
        return None, "unknown"
    estimated = text.startswith(("(", "[")) and text.endswith((")", "]"))
    inner = text[1:-1].strip() if estimated else text
    if inner.lower() in ("tr", "trace"):
        return 0.0, "trace"
    match = _NUMBER.match(inner.replace(",", ""))
    if match:
        return float(match[1]), "estimated" if estimated else ""
    return None, "unreadable"


def _norm(cell) -> str:
    return " ".join(str(cell).split()).strip() if cell is not None else ""


def _find_sheet(workbook, wanted: str):
    for name in workbook.sheetnames:
        lowered = name.lower()
        if wanted in lowered and not (wanted == "vitamins" and "fraction" in lowered):
            return workbook[name]
    return None


def _layout(rows: list[tuple]) -> tuple[dict, dict, int, str]:
    best_row, best_hits, best_match, method = -1, 0, _by_code, "codes"
    for label, match in (("codes", _by_code), ("column titles", _by_label)):
        for index, row in enumerate(rows):
            hits = len({match(cell) for cell in row} - {None})
            if hits > best_hits:
                best_row, best_hits, best_match, method = index, hits, match, label
        if best_hits >= 2:
            break
    if best_hits < 2:
        raise ImportProblem("could not find the nutrient columns, such as PROT, FAT and CHO")
    nutrient_columns = {}
    for column, cell in enumerate(rows[best_row]):
        key = best_match(cell)
        if key and key not in nutrient_columns.values():
            nutrient_columns[column] = key
    identity, last_header = {}, best_row
    for index, row in enumerate(rows):
        for column, cell in enumerate(row):
            role = IDENTITY.get(_norm(cell).lower())
            if role and role not in identity:
                identity[role] = column
                last_header = max(last_header, index)
    if "code" not in identity or "name" not in identity:
        raise ImportProblem('could not find the "Food Code" and "Food Name" columns')
    return identity, nutrient_columns, last_header + 1, method


def parse(data: bytes) -> tuple[list[ParsedFood], dict]:
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ImportProblem("That file could not be opened as an Excel workbook (.xlsx).") from exc
    foods: dict[str, ParsedFood] = {}
    report: dict = {"sheets": [], "unreadable_cells": 0, "skipped_rows": 0}
    try:
        for wanted in SHEETS:
            sheet = _find_sheet(workbook, wanted)
            if sheet is None:
                if wanted == "proximates":
                    raise ImportProblem(
                        'There is no "Proximates" sheet in that workbook. Is it the CoFID file from gov.uk?'
                    )
                report["sheets"].append({"wanted": wanted, "found": ""})
                continue
            rows = sheet.iter_rows(values_only=True)
            head = [row for _, row in zip(range(HEADER_SCAN_ROWS), rows)]
            try:
                identity, nutrient_columns, first_data, method = _layout(head)
            except ImportProblem as exc:
                raise ImportProblem(f'Sheet "{sheet.title}": {exc}.') from exc
            count, first_food_row = 0, None
            for row_number, row in enumerate([*head[first_data:], *rows], first_data + 1):
                def at(role: str) -> str:
                    column = identity.get(role)
                    return _norm(row[column]) if column is not None and column < len(row) else ""

                code, name = at("code"), at("name")
                if not code or not name:
                    if first_food_row and any(cell not in (None, "") for cell in row):
                        report["skipped_rows"] += 1
                    continue
                food = foods.get(code)
                if food is None:
                    if wanted != "proximates":
                        report["skipped_rows"] += 1
                        continue
                    food = foods[code] = ParsedFood(code, name, at("description"), at("group"))
                for column, key in nutrient_columns.items():
                    value, flag = parse_value(row[column] if column < len(row) else None)
                    if flag == "unreadable":
                        report["unreadable_cells"] += 1
                        flag = "unknown"
                    if flag == "blank":
                        continue
                    food.nutrients[key] = value
                    if flag:
                        food.flags[key] = flag
                count += 1
                first_food_row = first_food_row or row_number
            report["sheets"].append(
                {
                    "wanted": wanted,
                    "found": sheet.title,
                    "first_data_row": first_food_row,
                    "matched_by": method,
                    "rows": count,
                    "nutrients": sorted(nutrient_columns.values()),
                }
            )
    finally:
        workbook.close()
    parsed = list(foods.values())
    if not parsed:
        raise ImportProblem("No foods were found in that workbook.")
    mapped = {key for sheet in report["sheets"] for key in sheet.get("nutrients", [])}
    report["foods"] = len(parsed)
    report["nutrients_found"] = len(mapped)
    report["nutrients_missing"] = [n.label for n in NUTRIENTS if n.key not in mapped]
    checked = mismatched = 0
    for food in parsed:
        expected = energy_from_macros(food.nutrients)
        stated = food.nutrients.get("energy_kcal")
        if expected is None or stated is None:
            continue
        checked += 1
        if abs(expected - stated) > max(15.0, 0.1 * stated):
            mismatched += 1
    report["energy_checked"] = checked
    report["energy_mismatched"] = mismatched
    return parsed, report
