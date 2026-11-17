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
IDENTITY = {
    "foodcode": "code",
    "code": "code",
    "foodname": "name",
    "name": "name",
    "description": "description",
    "group": "group",
    "foodgroup": "group",
}
CODE_SHAPE = re.compile(r"^\d{1,3}-\d{1,4}[a-z]?$|^\d{1,6}[a-z]?$", re.IGNORECASE)
SAMPLE_ROWS = 400
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


def _compact(cell) -> str:
    return re.sub(r"[^a-z]", "", _norm(cell).lower())


def _code(cell) -> str:
    if isinstance(cell, float) and cell.is_integer():
        cell = int(cell)
    return _norm(cell)


def _find_sheet(workbook, wanted: str):
    for name in workbook.sheetnames:
        lowered = name.lower()
        if wanted in lowered and not (wanted == "vitamins" and "fraction" in lowered):
            return workbook[name]
    return None


def _nutrient_columns(rows: list[tuple]) -> tuple[dict, int, str]:
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
    columns = {}
    for column, cell in enumerate(rows[best_row]):
        key = best_match(cell)
        if key and key not in columns.values():
            columns[column] = key
    return columns, best_row, method


def _headings(rows: list[tuple], skip: set[int]) -> tuple[dict, int]:
    identity, last = {}, -1
    for index, row in enumerate(rows):
        for column, cell in enumerate(row):
            role = IDENTITY.get(_compact(cell))
            if role and role not in identity and column not in skip:
                identity[role] = column
                last = max(last, index)
    return identity, last


def _infer(rows: list[tuple], skip: set[int], known: set[str]) -> dict:
    width = min(max((len(row) for row in rows), default=0), 15)
    candidates = [column for column in range(width) if column not in skip]

    def hits(column: int, test) -> int:
        return sum(1 for row in rows if column < len(row) and test(row[column]))

    if known:
        def looks_like_code(cell) -> bool:
            return _code(cell) in known
    else:
        def looks_like_code(cell) -> bool:
            return bool(CODE_SHAPE.match(_code(cell)))

    scored = sorted(((hits(column, looks_like_code), -column) for column in candidates), reverse=True)
    filled = sum(1 for row in rows if any(cell not in (None, "") for cell in row))
    if not scored or scored[0][0] < max(1 if known else 3, filled // 2):
        return {}
    code_hits, code_column = scored[0][0], -scored[0][1]
    identity = {"code": code_column}

    def looks_like_name(cell) -> bool:
        text = _norm(cell)
        return len(text) >= 3 and any(ch.isalpha() for ch in text) and not CODE_SHAPE.match(text)

    for column in sorted(candidates, key=lambda c: (c < code_column, abs(c - code_column))):
        if column != code_column and hits(column, looks_like_name) >= code_hits / 2:
            identity["name"] = column
            break
    return identity


def _preview(rows: list[tuple]) -> str:
    shown = []
    for row in rows:
        cells = [_norm(cell)[:25] for cell in row[:8]]
        if any(cells):
            shown.append(" | ".join(cell or "(empty)" for cell in cells))
        if len(shown) == 3:
            break
    return " / ".join(shown) or "nothing"


def parse(data: bytes) -> tuple[list[ParsedFood], dict]:
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ImportProblem("That file could not be opened as an Excel workbook (.xlsx).") from exc
    foods: dict[str, ParsedFood] = {}
    report: dict = {"sheets": [], "unreadable_cells": 0, "skipped_rows": 0}
    try:
        for wanted in SHEETS:
            primary = wanted == "proximates"
            sheet = _find_sheet(workbook, wanted)
            if sheet is None:
                if primary:
                    raise ImportProblem(
                        'There is no "Proximates" sheet in that workbook. Is it the CoFID file from gov.uk?'
                    )
                report["sheets"].append({"wanted": wanted, "found": ""})
                continue
            rows = list(sheet.iter_rows(values_only=True))
            head = rows[:HEADER_SCAN_ROWS]
            try:
                nutrient_columns, nutrient_row, method = _nutrient_columns(head)
            except ImportProblem as exc:
                raise ImportProblem(f'Sheet "{sheet.title}": {exc}. The top of the sheet reads: {_preview(head)}') from exc
            skip = set(nutrient_columns)
            needs = ("code", "name") if primary else ("code",)
            identity, last_heading = _headings(head, skip)
            codes_found_by = "headings"
            if any(role not in identity for role in needs):
                sample = rows[nutrient_row + 1 : nutrient_row + 1 + SAMPLE_ROWS]
                identity = _infer(sample, skip, set() if primary else set(foods))
                last_heading, codes_found_by = -1, "matching codes"
                if any(role not in identity for role in needs):
                    wanted_columns = "food code and food name columns" if primary else "food code column"
                    raise ImportProblem(
                        f'Sheet "{sheet.title}": could not find the {wanted_columns}. '
                        f"The top of the sheet reads: {_preview(head)}"
                    )
            first_data = max(nutrient_row, last_heading) + 1
            count, first_food_row = 0, None
            for row_number, row in enumerate(rows[first_data:], first_data + 1):
                def at(role: str) -> str:
                    column = identity.get(role)
                    return _norm(row[column]) if column is not None and column < len(row) else ""

                code = _code(row[identity["code"]]) if identity["code"] < len(row) else ""
                name = at("name")
                if not code or (primary and not name):
                    if first_food_row and any(cell not in (None, "") for cell in row):
                        report["skipped_rows"] += 1
                    continue
                food = foods.get(code)
                if food is None:
                    if not primary:
                        if first_food_row:
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
                    "codes_found_by": codes_found_by,
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
