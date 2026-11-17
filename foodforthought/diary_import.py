from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

from openpyxl import load_workbook

from . import fmt
from .matching import parse_quantity, split_amount

MEALS = ("Breakfast", "Morning snack", "Lunch", "Afternoon snack", "Evening meal", "Evening snack", "Snacks", "Drinks", "Other")
MEAL_WORDS = {
    "breakfast": "Breakfast",
    "morning snack": "Morning snack",
    "mid morning": "Morning snack",
    "lunch": "Lunch",
    "afternoon snack": "Afternoon snack",
    "dinner": "Evening meal",
    "evening meal": "Evening meal",
    "supper": "Evening meal",
    "tea": "Evening meal",
    "evening snack": "Evening snack",
    "snack": "Snacks",
    "snacks": "Snacks",
    "drink": "Drinks",
    "drinks": "Drinks",
}
MAX_ROWS = 2000


class DiaryImportProblem(Exception):
    pass


@dataclass
class RawEntry:
    row: int
    day: date
    meal: str
    text: str
    amount_text: str
    quantity: float | None
    unit: str
    detail: str = ""
    app_kcal: float | None = None


def meal_name(text: str) -> str:
    cleaned = " ".join(str(text or "").lower().replace("-", " ").split())
    return MEAL_WORDS.get(cleaned, str(text).strip().capitalize() if cleaned else "Other")


def as_date(cell) -> date | None:
    if isinstance(cell, datetime):
        return cell.date()
    if isinstance(cell, date):
        return cell
    return fmt.parse_date(str(cell).strip()) if cell not in (None, "") else None


def _rows_from_csv(data: bytes) -> list[list]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [row for _, row in zip(range(MAX_ROWS + 50), csv.reader(io.StringIO(text), dialect))]


def _header_map(header: list, wanted: dict[str, tuple[str, ...]]) -> dict[str, int]:
    names = [" ".join(str(cell or "").lower().split()) for cell in header]
    found = {}
    for role, options in wanted.items():
        for index, name in enumerate(names):
            if name in options or name.split("(")[0].strip().rstrip(":") in options:
                found[role] = index
                break
    return found


class Importer:
    key = ""
    label = ""
    help = ""

    def accepts(self, filename: str, data: bytes) -> bool:
        raise NotImplementedError

    def parse(self, filename: str, data: bytes) -> list[RawEntry]:
        raise NotImplementedError


class TemplateImporter(Importer):
    key = "template"
    label = "FoodForThought diary template"
    help = "The spreadsheet from Send a diary template, filled in by the client. Excel (.xlsx) or CSV."
    COLUMNS = {
        "date": ("date", "day"),
        "meal": ("meal", "meal or time"),
        "food": ("food or drink", "food", "food and drink"),
        "brand": ("brand", "brand (if known)"),
        "amount": ("how much", "amount", "quantity"),
        "unit": ("unit", "units"),
        "notes": ("notes", "note", "how it was cooked"),
    }

    def _rows(self, filename: str, data: bytes) -> list[list]:
        if filename.lower().endswith(".csv"):
            return _rows_from_csv(data)
        try:
            book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:
            raise DiaryImportProblem("That file could not be opened as a spreadsheet.") from exc
        try:
            for sheet in book.worksheets:
                rows = [list(row) for _, row in zip(range(MAX_ROWS + 50), sheet.iter_rows(values_only=True))]
                if any(self._header_index(rows[:10]) is not None for _ in [0]):
                    return rows
        finally:
            book.close()
        return []

    def _header_index(self, rows: list[list]) -> int | None:
        for index, row in enumerate(rows):
            found = _header_map(row, self.COLUMNS)
            if "food" in found and "amount" in found:
                return index
        return None

    def accepts(self, filename: str, data: bytes) -> bool:
        if not filename.lower().endswith((".xlsx", ".csv")):
            return False
        try:
            rows = self._rows(filename, data)
        except DiaryImportProblem:
            return False
        return self._header_index(rows[:10]) is not None

    def parse(self, filename: str, data: bytes) -> list[RawEntry]:
        rows = self._rows(filename, data)
        header_at = self._header_index(rows[:10])
        if header_at is None:
            raise DiaryImportProblem('No "Food or drink" and "How much" columns were found.')
        columns = _header_map(rows[header_at], self.COLUMNS)
        entries, last_day = [], None

        def cell(row: list, role: str):
            index = columns.get(role)
            return row[index] if index is not None and index < len(row) else None

        for number, row in enumerate(rows[header_at + 1 :], header_at + 2):
            food = " ".join(str(cell(row, "food") or "").split())
            if not food:
                continue
            day = as_date(cell(row, "date")) or last_day
            if day is None:
                raise DiaryImportProblem(f"Row {number} has no date, and no earlier row has one either.")
            last_day = day
            raw_amount = cell(row, "amount")
            unit = " ".join(str(cell(row, "unit") or "").split())
            amount_text = " ".join(f"{'' if raw_amount is None else raw_amount} {unit}".split())
            quantity = parse_quantity(raw_amount)
            if quantity is None and isinstance(raw_amount, str):
                quantity, extra_unit = split_amount(raw_amount)
                unit = unit or extra_unit
            details = [str(cell(row, role) or "").strip() for role in ("brand", "notes")]
            entries.append(
                RawEntry(
                    row=number,
                    day=day,
                    meal=meal_name(cell(row, "meal")),
                    text=food,
                    amount_text=amount_text,
                    quantity=quantity,
                    unit=unit,
                    detail=" · ".join(part for part in details if part),
                )
            )
        return entries


class CronometerImporter(Importer):
    key = "cronometer"
    label = "Cronometer"
    help = (
        'In Cronometer: More, Account, Account Data, Export Data, choose the dates, then "Export Food & Recipe Entries" '
        "(called Export Servings in older versions). Upload the .csv file it downloads."
    )
    COLUMNS = {
        "day": ("day", "date"),
        "group": ("group", "diary group", "meal"),
        "food": ("food name",),
        "amount": ("amount",),
        "kcal": ("energy (kcal)",),
    }

    def accepts(self, filename: str, data: bytes) -> bool:
        if not filename.lower().endswith(".csv"):
            return False
        rows = _rows_from_csv(data[:20000])
        if not rows:
            return False
        found = _header_map(rows[0], self.COLUMNS)
        return {"day", "food", "amount"} <= set(found)

    def parse(self, filename: str, data: bytes) -> list[RawEntry]:
        rows = _rows_from_csv(data)
        columns = _header_map(rows[0], self.COLUMNS) if rows else {}
        if not {"day", "food", "amount"} <= set(columns):
            raise DiaryImportProblem('This is not a Cronometer "Food & Recipe Entries" export.')

        def cell(row: list, role: str) -> str:
            index = columns.get(role)
            return row[index].strip() if index is not None and index < len(row) and row[index] else ""

        entries = []
        for number, row in enumerate(rows[1:], 2):
            food = cell(row, "food")
            if not food:
                continue
            day = as_date(cell(row, "day"))
            if day is None:
                raise DiaryImportProblem(f'Row {number} has a date that could not be read: "{cell(row, "day")}".')
            amount_text = cell(row, "amount")
            quantity, unit = split_amount(amount_text)
            kcal = parse_quantity(cell(row, "kcal")) if "kcal" in columns else None
            entries.append(
                RawEntry(
                    row=number,
                    day=day,
                    meal=meal_name(cell(row, "group")),
                    text=food,
                    amount_text=amount_text,
                    quantity=quantity,
                    unit=unit,
                    app_kcal=kcal,
                )
            )
        return entries


IMPORTERS: tuple[Importer, ...] = (CronometerImporter(), TemplateImporter())


def detect(filename: str, data: bytes) -> Importer:
    for importer in IMPORTERS:
        if importer.accepts(filename, data):
            return importer
    raise DiaryImportProblem(
        "That file was not recognised. Upload a filled-in FoodForThought diary template (.xlsx or .csv) "
        'or a Cronometer "Food & Recipe Entries" export (.csv).'
    )
