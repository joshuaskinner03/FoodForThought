from __future__ import annotations

import io
from datetime import date, timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from .diary_import import MEALS

UNITS = ("g", "ml", "slice", "teaspoon", "tablespoon", "cup", "glass", "mug", "bowl", "piece", "small", "medium", "large", "pack", "can", "bottle", "handful", "portion")
HEADERS = ("Date", "Meal", "Food or drink", "Brand (if known)", "How much", "Unit", "Notes (how it was cooked, what was added)")
WIDTHS = (13, 16, 40, 20, 11, 12, 44)
INSTRUCTIONS = (
    "How to fill in your food diary",
    "",
    "Write down everything you eat and drink, including snacks, drinks, sauces, spreads, oils and sugar in tea or coffee.",
    "Fill it in as you go through the day rather than from memory at night.",
    "Use one row for each food or drink. Each day already has its date; use as many rows as you need.",
    "Meal: choose from the list.",
    "How much: a number. Unit: choose from the list, or type your own (for example: 2 slices, 1 medium, 250 ml).",
    "If you know the weight in grams from the pack or a scale, use g. That is the most accurate.",
    "Brand: helpful for packaged foods, for example the shop or maker's name.",
    "Notes: how it was cooked (fried, grilled, boiled), and anything added (butter, milk, sugar).",
    "Please do not add rows above the column headings or change the headings.",
    "Save the file and send it back to me when the diary is finished.",
)


def build(start: date, days: int, rows_per_day: int = 20) -> bytes:
    book = Workbook()
    diary = book.active
    diary.title = "Food diary"
    header_fill = PatternFill("solid", fgColor="2F6B4F")
    for column, (title, width) in enumerate(zip(HEADERS, WIDTHS), 1):
        cell = diary.cell(row=1, column=column, value=title)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        diary.column_dimensions[cell.column_letter].width = width
    diary.row_dimensions[1].height = 32
    diary.freeze_panes = "A2"
    shade = PatternFill("solid", fgColor="EEF4F0")
    row = 2
    for offset in range(days):
        for line in range(rows_per_day):
            cell = diary.cell(row=row, column=1, value=start + timedelta(days=offset))
            cell.number_format = "DD/MM/YYYY"
            if offset % 2:
                for column in range(1, len(HEADERS) + 1):
                    diary.cell(row=row, column=column).fill = shade
            row += 1
    last = row - 1
    meals = DataValidation(type="list", formula1='"' + ",".join(MEALS) + '"', allow_blank=True)
    meals.error, meals.errorTitle, meals.showErrorMessage = "Choose a meal from the list.", "Meal", True
    units = DataValidation(type="list", formula1='"' + ",".join(UNITS) + '"', allow_blank=True)
    units.showErrorMessage = False
    amounts = DataValidation(type="decimal", operator="greaterThan", formula1="0", allow_blank=True)
    amounts.error, amounts.errorTitle, amounts.showErrorMessage = "Type a number, for example 2 or 0.5.", "How much", True
    for validation, column in ((meals, "B"), (units, "F"), (amounts, "E")):
        diary.add_data_validation(validation)
        validation.add(f"{column}2:{column}{last}")
    guide = book.create_sheet("How to fill this in")
    guide.column_dimensions["A"].width = 110
    for index, line in enumerate(INSTRUCTIONS, 1):
        cell = guide.cell(row=index, column=1, value=line)
        cell.alignment = Alignment(wrap_text=True)
        if index == 1:
            cell.font = Font(bold=True, size=14)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
