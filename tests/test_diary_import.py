import io
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from foodforthought.diary_import import CronometerImporter, DiaryImportProblem, TemplateImporter, detect, meal_name
from foodforthought.diary_template import build

CRONOMETER_NEW = (
    "Day,Time,Group,Food Name,Amount,Category\r\n"
    "2026-11-10,08:05,Breakfast,\"Milk, semi-skimmed\",200.00 ml,Dairy\r\n"
    "2026-11-10,08:05,Breakfast,Porridge oats,1.00 cup,Grains\r\n"
    "2026-11-11,13:00,Lunch,Wholemeal bread,2.00 slice,Grains\r\n"
    "2026-11-11,19:00,Dinner,Red wine,1.00 glass,Drinks\r\n"
    "2026-11-11,19:00,Snacks,,1.00 g,\r\n"
)
CRONOMETER_OLD = (
    "Day,Group,Food Name,Amount,Energy (kcal),Protein (g)\n"
    "2026-11-10,Breakfast,Porridge oats,1.00 cup,180.00,6.00\n"
)


def filled_template(rows) -> bytes:
    book = load_workbook(io.BytesIO(build(date(2026, 11, 10), 2, rows_per_day=3)))
    sheet = book["Food diary"]
    for offset, values in enumerate(rows, 2):
        for column, value in enumerate(values, 2):
            sheet.cell(row=offset, column=column, value=value)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_template_has_dates_dropdowns_and_instructions():
    book = load_workbook(io.BytesIO(build(date(2026, 11, 10), 3, rows_per_day=4)))
    sheet = book["Food diary"]
    assert [c.value for c in sheet[1]][:6] == ["Date", "Meal", "Food or drink", "Brand (if known)", "How much", "Unit"]
    dates = [sheet.cell(row=row, column=1).value for row in range(2, 14)]
    assert dates[0] == datetime(2026, 11, 10) and dates[4] == datetime(2026, 11, 11) and dates[-1] == datetime(2026, 11, 12)
    assert sheet.cell(row=14, column=1).value is None
    assert len(sheet.data_validations.dataValidation) == 3
    assert "How to fill this in" in book.sheetnames


def test_template_import():
    data = filled_template(
        [
            ("Breakfast", "Semi skimmed milk", "Tesco", 200, "ml", "in tea"),
            ("Breakfast", "Porridge", None, "1/2", "cup", None),
            (None, None, None, None, None, None),
            ("Dinner", "Wholemeal bread", None, 2, "slices", "toasted"),
            ("Lunch", "Apple", None, "one", "medium", None),
        ]
    )
    importer = detect("my diary.xlsx", data)
    assert importer.key == "template"
    entries = importer.parse("my diary.xlsx", data)
    assert [e.text for e in entries] == ["Semi skimmed milk", "Porridge", "Wholemeal bread", "Apple"]
    milk, porridge, bread, apple = entries
    assert milk.day == date(2026, 11, 10) and milk.meal == "Breakfast" and milk.quantity == 200 and milk.unit == "ml"
    assert milk.detail == "Tesco · in tea" and milk.amount_text == "200 ml"
    assert porridge.quantity == 0.5 and porridge.unit == "cup"
    assert bread.day == date(2026, 11, 11) and bread.meal == "Evening meal" and bread.row == 5
    assert apple.day == date(2026, 11, 11) and apple.quantity == 1


def test_template_csv_and_carried_dates():
    data = (
        "Date,Meal,Food or drink,Brand (if known),How much,Unit,Notes\n"
        "10/11/2026,Breakfast,Toast,,2,slice,\n"
        ",Breakfast,Butter,,10,g,\n"
        "11/11/2026,lunch,Soup,,1,bowl,\n"
    ).encode()
    entries = detect("diary.csv", data).parse("diary.csv", data)
    assert [(e.text, e.day) for e in entries] == [("Toast", date(2026, 11, 10)), ("Butter", date(2026, 11, 10)), ("Soup", date(2026, 11, 11))]
    assert entries[2].meal == "Lunch"
    with pytest.raises(DiaryImportProblem, match="Row 2 has no date"):
        TemplateImporter().parse("x.csv", b"Date,Meal,Food or drink,How much,Unit\n,Lunch,Soup,1,bowl\n")


def test_cronometer_import():
    data = CRONOMETER_NEW.encode()
    importer = detect("servings.csv", data)
    assert importer.key == "cronometer"
    entries = importer.parse("servings.csv", data)
    assert len(entries) == 4
    milk, oats, bread, wine = entries
    assert milk.text == "Milk, semi-skimmed" and milk.quantity == 200 and milk.unit == "ml" and milk.meal == "Breakfast"
    assert oats.quantity == 1 and oats.unit == "cup" and oats.app_kcal is None
    assert bread.day == date(2026, 11, 11) and bread.unit == "slice" and wine.meal == "Evening meal"
    old = CronometerImporter().parse("servings.csv", CRONOMETER_OLD.encode())
    assert old[0].app_kcal == 180.0


def test_detection_rejects_other_files():
    for name, data in (("notes.txt", b"hello"), ("x.csv", b"a,b,c\n1,2,3\n"), ("x.xlsx", b"not excel")):
        with pytest.raises(DiaryImportProblem, match="not recognised"):
            detect(name, data)
    with pytest.raises(DiaryImportProblem, match="could not be read"):
        CronometerImporter().parse("x.csv", b"Day,Group,Food Name,Amount\nyesterday,Lunch,Soup,1 bowl\n")


def test_meal_names():
    assert [meal_name(m) for m in ("Dinner", "snacks", "SUPPER", "Mid-morning", "", "Elevenses")] == [
        "Evening meal", "Snacks", "Evening meal", "Morning snack", "Other", "Elevenses",
    ]
