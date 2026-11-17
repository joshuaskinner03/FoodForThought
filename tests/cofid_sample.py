import io

from openpyxl import Workbook

IDENTITY = ["Food Code", "Food Name", "Description", "Group", "Previous", "Main data references", "Footnote"]
SHEETS = {
    "Proximates": (
        ["Water (g)", "Protein (g)", "Fat (g)", "Carbohydrate (g)", "Energy (kcal) (kcal)", "Energy (kJ) (kJ)", "Total sugars (g)", "Alcohol (g)", "NSP (g)", "AOAC fibre (g)", "Satd FA /100g fd (g)", "Cholesterol (mg)"],
        ["WATER", "PROT", "FAT", "CHO", "KCALS", "KJ", "TOTSUG", "ALCO", "ENGFIB", "AOACFIB", "SATFOD", "CHOL"],
        [
            ["11-001", "Porridge, made with water", "Rolled oats and water", "AC", 87.1, 1.5, 1.1, 8.1, 46, 195, "Tr", 0, 0.8, 1.1, 0.2, 0],
            ["12-001", "Milk, semi-skimmed, pasteurised, average", "", "BA", 89.4, 3.5, 1.7, 4.7, 46, 195, 4.7, 0, 0, 0, 1.1, 6],
            ["17-001", "Wine, red", "", "QA", 88.5, 0.1, 0, 0.2, 68, 283, 0.2, 9.6, 0, "N", 0, 0],
            ["14-001", "Bread, wholemeal, average", "", "A", 41.2, 9.4, 2.5, "42.0", 217, 922, 2.8, 0, 5.0, "(7.0)", 0.5, 0],
            ["99-999", "Wrong energy, test food", "", "W", 50, 10, 10, 10, 900, 3700, 1, 0, 0, 0, 1, 0],
            ["", "Row with no code", "", "", 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
        ],
    ),
    "Inorganics": (
        ["Sodium (mg)", "Potassium (mg)", "Calcium (mg)", "Iron (mg)", "Iodine (µg)"],
        ["NA", "K", "CA", "FE", "I"],
        [
            ["11-001", "Porridge, made with water", "", "AC", "Tr", 46, 7, 0.5, "N"],
            ["12-001", "Milk, semi-skimmed, pasteurised, average", "", "BA", 43, 156, 120, 0.02, 30],
            ["55-555", "Only in inorganics", "", "D", 1, 1, 1, 1, 1],
        ],
    ),
    "Vitamins": (
        ["Retinol (µg)", "Retinol Equivalent (µg)", "Vitamin D (µg)", "Riboflavin (mg)", "Niacin (mg)", "Niacin equivalent (mg)", "Vitamin B12 (µg)", "Folate (µg)", "Vitamin C (mg)"],
        ["RET", "RETEQU", "VITD", "RIBO", "NIAC", "NIACEQU", "VITB12", "FOLT", "VITC"],
        [
            ["12-001", "Milk, semi-skimmed, pasteurised, average", "", "BA", 19, 20, "Tr", "(0.24)", 0.1, 0.9, 0.9, 9, 2],
            ["11-001", "Porridge, made with water", "", "AC", 0, 0, 0, 0.01, 0.1, 0.4, 0, 4, 0],
        ],
    ),
    "Vitamin Fractions": (["Alpha-carotene (µg)"], ["ACAR"], [["12-001", "Milk, semi-skimmed, pasteurised, average", "", "BA", 999]]),
}


def _numeric(code):
    return int(code.replace("-", "")) if isinstance(code, str) and code.replace("-", "").isdigit() else code


def workbook(
    numbered: bool = True,
    code_row: int | None = 2,
    extra_top_rows: int = 0,
    primary_identity: list | None = None,
    secondary_identity: list | None = None,
    numeric_codes: bool = False,
) -> bytes:
    book = Workbook()
    book.active.title = "1.1 Notes" if numbered else "Notes"
    book.active.append(["McCance and Widdowson's Composition of Foods Integrated Dataset"])
    for number, (name, (labels, codes, rows)) in enumerate(SHEETS.items(), 3):
        primary = name == "Proximates"
        IDENTITY = (primary_identity if primary else secondary_identity) or globals()["IDENTITY"]
        if numeric_codes:
            rows = [[_numeric(row[0]) if primary else float(_numeric(row[0]) or 0) or row[0], *row[1:]] for row in rows]
        sheet = book.create_sheet(f"1.{number} {name}" if numbered else name)
        for _ in range(extra_top_rows):
            sheet.append(["Explanatory text"])
        header = [IDENTITY + labels, [None] * len(IDENTITY) + [None] * len(labels), [None] * len(IDENTITY) + [None] * len(labels)]
        if code_row:
            header[code_row - 1] = (IDENTITY if code_row == 1 else [None] * len(IDENTITY)) + codes
            if code_row == 1:
                header[1] = [None] * len(IDENTITY) + labels
        for row in header:
            sheet.append(row)
        for row in rows:
            sheet.append(row[:4] + [None, None, None] + row[4:])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
