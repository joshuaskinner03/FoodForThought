from __future__ import annotations

from dataclasses import dataclass

# RNIs: Public Health England, Government Dietary Recommendations (2016), tables 1 to 6.
# LRNIs: Department of Health, Dietary Reference Values: A Guide (1991), from COMA 1991.
# Pregnancy and breastfeeding increments: COMA 1991; protein increments as given by the
# British Nutrition Foundation, Nutrition Requirements (2021).

BANDS = ("1", "2-3", "4-6", "7-10", "11-14", "15-18", "19-64", "65-74", "75+")
LIFE_STAGES = (
    ("", "Not pregnant or breastfeeding"),
    ("pregnant", "Pregnant, first or second trimester"),
    ("pregnant_late", "Pregnant, third trimester"),
    ("breastfeeding_0_4", "Breastfeeding, baby under 4 months"),
    ("breastfeeding_4_6", "Breastfeeding, baby 4 to 6 months"),
    ("breastfeeding_6", "Breastfeeding, baby over 6 months"),
)


def band(age: int) -> str | None:
    if age < 1:
        return None
    for name, upper in (("1", 2), ("2-3", 4), ("4-6", 7), ("7-10", 11), ("11-14", 15), ("15-18", 19), ("19-64", 65), ("65-74", 75)):
        if age < upper:
            return name
    return "75+"


def _same(*values):
    return {"male": values, "female": values}


def _split(male, female):
    return {"male": male, "female": female}


# Each row: one value per band in BANDS order; None means no value is set.
RNI = {
    "vitamin_a": _split((400, 400, 400, 500, 600, 700, 700, 700, 700), (400, 400, 400, 500, 600, 600, 600, 600, 600)),
    "thiamin": _split((0.3, 0.4, 0.6, 0.7, 1.0, 1.0, 1.0, 0.9, 0.9), (0.3, 0.4, 0.6, 0.7, 0.8, 0.8, 0.8, 0.8, 0.7)),
    "riboflavin": _split((0.6, 0.6, 0.8, 1.0, 1.2, 1.3, 1.3, 1.3, 1.3), (0.6, 0.6, 0.8, 1.0, 1.1, 1.1, 1.1, 1.1, 1.1)),
    "niacin_equivalent": _split((5.0, 7.2, 9.8, 12.0, 16.5, 16.5, 16.5, 15.5, 15.1), (4.7, 6.6, 9.1, 11.2, 13.2, 13.2, 13.2, 12.6, 12.1)),
    "vitamin_b6": _split((0.7, 0.7, 0.9, 1.0, 1.2, 1.5, 1.4, 1.4, 1.4), (0.7, 0.7, 0.9, 1.0, 1.0, 1.2, 1.2, 1.2, 1.2)),
    "vitamin_b12": _same(0.5, 0.5, 0.8, 1.0, 1.2, 1.5, 1.5, 1.5, 1.5),
    "folate": _same(70, 70, 100, 150, 200, 200, 200, 200, 200),
    "vitamin_c": _same(30, 30, 30, 30, 35, 40, 40, 40, 40),
    "vitamin_d": _same(10, 10, 10, 10, 10, 10, 10, 10, 10),
    "iron": _split((6.9, 6.9, 6.1, 8.7, 11.3, 11.3, 8.7, 8.7, 8.7), (6.9, 6.9, 6.1, 8.7, 14.8, 14.8, 14.8, 8.7, 8.7)),
    "calcium": _split((350, 350, 450, 550, 1000, 1000, 700, 700, 700), (350, 350, 450, 550, 800, 800, 700, 700, 700)),
    "phosphorus": _split((270, 270, 350, 450, 775, 775, 550, 550, 550), (270, 270, 350, 450, 625, 625, 550, 550, 550)),
    "magnesium": _split((85, 85, 120, 200, 280, 300, 300, 300, 300), (85, 85, 120, 200, 280, 300, 270, 270, 270)),
    "potassium": _same(800, 800, 1100, 2000, 3100, 3500, 3500, 3500, 3500),
    "zinc": _split((5.0, 5.0, 6.5, 7.0, 9.0, 9.5, 9.5, 9.5, 9.5), (5.0, 5.0, 6.5, 7.0, 9.0, 7.0, 7.0, 7.0, 7.0)),
    "copper": _same(0.4, 0.4, 0.6, 0.7, 0.8, 1.0, 1.2, 1.2, 1.2),
    "selenium": _split((15, 15, 20, 30, 45, 70, 75, 75, 75), (15, 15, 20, 30, 45, 60, 60, 60, 60)),
    "iodine": _same(70, 70, 100, 110, 130, 140, 140, 140, 140),
}

# Calcium and phosphorus at 11-18 and magnesium at 11-14 are left out: the scanned guide
# and other sources disagree, so no figure is used rather than a possibly wrong one.
LRNI = {
    "vitamin_a": _split((200, 200, 200, 250, 250, 300, 300, 300, 300), (200, 200, 200, 250, 250, 250, 250, 250, 250)),
    "riboflavin": _same(0.3, 0.3, 0.4, 0.5, 0.8, 0.8, 0.8, 0.8, 0.8),
    "vitamin_b12": _same(0.3, 0.3, 0.5, 0.6, 0.8, 1.0, 1.0, 1.0, 1.0),
    "folate": _same(35, 35, 50, 75, 100, 100, 100, 100, 100),
    "vitamin_c": _same(8, 8, 8, 8, 9, 10, 10, 10, 10),
    "iron": _split((3.7, 3.7, 3.3, 4.7, 6.1, 6.1, 4.7, 4.7, 4.7), (3.7, 3.7, 3.3, 4.7, 8.0, 8.0, 8.0, 4.7, 4.7)),
    "calcium": _same(200, 200, 275, 325, None, None, 400, 400, 400),
    "phosphorus": _same(155, 155, 215, 250, None, None, 310, 310, 310),
    "magnesium": _split((50, 50, 70, 115, None, 190, 190, 190, 190), (50, 50, 70, 115, None, 190, 150, 150, 150)),
    "potassium": _same(450, 450, 600, 950, 1600, 2000, 2000, 2000, 2000),
    "zinc": _split((3.0, 3.0, 4.0, 4.0, 5.3, 5.5, 5.5, 5.5, 5.5), (3.0, 3.0, 4.0, 4.0, 5.3, 4.0, 4.0, 4.0, 4.0)),
    "selenium": _same(7, 7, 10, 16, 25, 40, 40, 40, 40),
    "iodine": _same(40, 40, 50, 55, 65, 70, 70, 70, 70),
}
# These LRNIs are set per 1000 kcal (thiamin, niacin) or per g protein (B6), on the same basis
# as the RNI, so the LRNI is the RNI scaled by the ratio of the two rates.
LRNI_RATIO = {"thiamin": 0.23 / 0.40, "niacin_equivalent": 4.4 / 6.6, "vitamin_b6": 11 / 15}

PREGNANT = {"riboflavin": 0.3, "folate": 100, "vitamin_a": 100}
PREGNANT_LATE = {"thiamin": 0.1, "vitamin_c": 10}
BREASTFEEDING = {
    "thiamin": 0.2,
    "riboflavin": 0.5,
    "niacin_equivalent": 2,
    "vitamin_b12": 0.5,
    "folate": 60,
    "vitamin_c": 30,
    "vitamin_a": 350,
    "calcium": 550,
    "phosphorus": 440,
    "magnesium": 50,
    "copper": 0.3,
    "selenium": 15,
}

PROTEIN_RNI = _split((14.5, 14.5, 19.7, 28.3, 42.1, 55.2, 55.5, 53.3, 53.3), (14.5, 14.5, 19.7, 28.3, 41.2, 45.0, 45.0, 46.5, 46.5))
FIBRE_G = {"2-3": 15, "4-6": 20, "7-10": 20, "11-14": 25, "15-18": 30, "19-64": 30, "65-74": 30, "75+": 30}
SALT_MAX_G = {"1": 2, "2-3": 2, "4-6": 3, "7-10": 5}
ORDER = tuple(RNI)


@dataclass(frozen=True)
class Group:
    sex: str
    age: int
    band: str
    life_stage: str

    @property
    def adult(self) -> bool:
        return self.age >= 19

    def describe(self) -> str:
        sex = {"female": "Female", "male": "Male"}[self.sex]
        stage = dict(LIFE_STAGES).get(self.life_stage, "")
        return f"{sex}, aged {self.age}" + (f", {stage[0].lower()}{stage[1:]}" if self.life_stage else "")


def group_for(sex: str, age: int | None, life_stage: str = "") -> Group | None:
    if sex not in ("female", "male") or age is None:
        return None
    name = band(age)
    if name is None:
        return None
    stage = life_stage if sex == "female" and life_stage in dict(LIFE_STAGES) else ""
    return Group(sex, age, name, stage)


def increments(life_stage: str) -> dict:
    if life_stage in ("pregnant", "pregnant_late"):
        found = dict(PREGNANT)
        if life_stage == "pregnant_late":
            found.update(PREGNANT_LATE)
        return found
    if life_stage.startswith("breastfeeding"):
        found = dict(BREASTFEEDING)
        found["zinc"] = 6.0 if life_stage == "breastfeeding_0_4" else 2.5
        return found
    return {}


def values(group: Group) -> dict[str, dict]:
    index = BANDS.index(group.band)
    extra = increments(group.life_stage)
    found = {}
    for key in ORDER:
        rni = RNI[key][group.sex][index]
        lrni = LRNI[key][group.sex][index] if key in LRNI else None
        if key == "iron" and group.sex == "female" and group.age >= 50:
            rni, lrni = 8.7, 4.7
        if key in LRNI_RATIO:
            lrni = round(rni * LRNI_RATIO[key], 2)
        rni_total = round(rni + extra.get(key, 0), 2)
        found[key] = {
            "rni": rni_total,
            "lrni": lrni,
            "raised": key in extra,
        }
    return found


def protein_rni(group: Group, weight_kg: float | None = None) -> tuple[float, str]:
    if group.adult and weight_kg:
        base, basis = 0.75 * weight_kg, f"0.75 g per kg of body weight ({weight_kg:g} kg)"
    else:
        base, basis = PROTEIN_RNI[group.sex][BANDS.index(group.band)], "for the age group"
    if group.life_stage.startswith("pregnant"):
        return base + 6, basis + ", plus 6 g in pregnancy"
    if group.life_stage in ("breastfeeding_0_4", "breastfeeding_4_6"):
        return base + 11, basis + ", plus 11 g when breastfeeding a baby under 6 months"
    if group.life_stage == "breastfeeding_6":
        return base + 8, basis + ", plus 8 g when breastfeeding a baby over 6 months"
    return base, basis


def fibre_g(age: int) -> float | None:
    if age < 2:
        return None
    if age == 4:
        return 15
    return FIBRE_G.get(band(age))


def salt_max_g(age: int) -> float | None:
    found = band(age)
    if found is None:
        return None
    return SALT_MAX_G.get(found, 6)
