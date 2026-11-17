import pytest

from foodforthought import reference as r


def values(sex, age, stage=""):
    return r.values(r.group_for(sex, age, stage))


def test_bands():
    assert r.band(0) is None
    assert [r.band(age) for age in (1, 2, 3, 4, 6, 7, 10, 11, 14, 15, 18, 19, 64, 65, 74, 75, 99)] == [
        "1", "2-3", "2-3", "4-6", "4-6", "7-10", "7-10", "11-14", "11-14", "15-18", "15-18", "19-64", "19-64", "65-74", "65-74", "75+", "75+",
    ]
    assert r.group_for("", 30) is None and r.group_for("female", None) is None and r.group_for("male", 0) is None
    assert r.group_for("male", 30, "pregnant").life_stage == ""


@pytest.mark.parametrize(
    "sex, age, key, lrni, rni",
    [
        ("male", 30, "vitamin_a", 300, 700),
        ("female", 30, "vitamin_a", 250, 600),
        ("female", 30, "iron", 8.0, 14.8),
        ("female", 49, "iron", 8.0, 14.8),
        ("female", 55, "iron", 4.7, 8.7),
        ("male", 30, "iron", 4.7, 8.7),
        ("female", 30, "calcium", 400, 700),
        ("male", 30, "magnesium", 190, 300),
        ("female", 30, "magnesium", 150, 270),
        ("male", 30, "zinc", 5.5, 9.5),
        ("female", 30, "zinc", 4.0, 7.0),
        ("female", 30, "selenium", 40, 60),
        ("male", 30, "selenium", 40, 75),
        ("female", 30, "iodine", 70, 140),
        ("female", 30, "folate", 100, 200),
        ("female", 30, "vitamin_b12", 1.0, 1.5),
        ("female", 30, "vitamin_c", 10, 40),
        ("female", 30, "riboflavin", 0.8, 1.1),
        ("female", 30, "potassium", 2000, 3500),
        ("female", 30, "copper", None, 1.2),
        ("female", 30, "vitamin_d", None, 10),
        ("male", 70, "thiamin", 0.52, 0.9),
        ("female", 80, "niacin_equivalent", 8.07, 12.1),
        ("female", 30, "vitamin_b6", 0.88, 1.2),
        ("male", 2, "iron", 3.7, 6.9),
        ("female", 5, "calcium", 275, 450),
        ("male", 8, "zinc", 4.0, 7.0),
        ("female", 12, "iron", 8.0, 14.8),
        ("male", 16, "calcium", None, 1000),
        ("female", 13, "magnesium", None, 280),
        ("female", 17, "magnesium", 190, 300),
        ("male", 1, "thiamin", 0.17, 0.3),
    ],
)
def test_reference_values(sex, age, key, lrni, rni):
    found = values(sex, age)[key]
    assert found["rni"] == rni
    assert found["lrni"] == lrni


def test_pregnancy_and_breastfeeding_raise_the_rni_only():
    plain = values("female", 30)
    early = values("female", 30, "pregnant")
    late = values("female", 30, "pregnant_late")
    assert early["folate"]["rni"] == 300 and early["vitamin_a"]["rni"] == 700 and early["riboflavin"]["rni"] == 1.4
    assert early["thiamin"]["rni"] == 0.8 and late["thiamin"]["rni"] == 0.9 and late["vitamin_c"]["rni"] == 50
    assert early["iron"]["rni"] == 14.8 and early["calcium"]["rni"] == 700
    assert early["folate"]["lrni"] == plain["folate"]["lrni"] and early["folate"]["raised"] and not early["iron"]["raised"]
    feeding = values("female", 30, "breastfeeding_0_4")
    later = values("female", 30, "breastfeeding_4_6")
    assert feeding["calcium"]["rni"] == 1250 and feeding["zinc"]["rni"] == 13.0 and later["zinc"]["rni"] == 9.5
    assert feeding["vitamin_a"]["rni"] == 950 and feeding["selenium"]["rni"] == 75 and feeding["niacin_equivalent"]["rni"] == 15.2
    assert feeding["iron"]["rni"] == 14.8 and feeding["iodine"]["rni"] == 140


def test_protein_fibre_salt():
    adult = r.group_for("female", 30)
    assert r.protein_rni(adult, 60)[0] == pytest.approx(45)
    assert r.protein_rni(adult)[0] == 45.0
    assert r.protein_rni(r.group_for("male", 70))[0] == 53.3
    assert r.protein_rni(r.group_for("female", 30, "pregnant"), 60)[0] == pytest.approx(51)
    assert r.protein_rni(r.group_for("female", 30, "breastfeeding_4_6"), 60)[0] == pytest.approx(56)
    assert r.protein_rni(r.group_for("female", 30, "breastfeeding_6"), 60)[0] == pytest.approx(53)
    assert r.protein_rni(r.group_for("male", 8), 30)[0] == 28.3
    assert [r.fibre_g(age) for age in (1, 2, 3, 4, 5, 10, 11, 15, 30)] == [None, 15, 15, 15, 20, 20, 25, 30, 30]
    assert [r.salt_max_g(age) for age in (0, 1, 3, 5, 9, 11, 40)] == [None, 2, 2, 3, 5, 6, 6]


def test_tables_are_complete_and_ordered():
    for table in (r.RNI, r.LRNI):
        for key, rows in table.items():
            assert set(rows) == {"male", "female"}
            for row in rows.values():
                assert len(row) == len(r.BANDS), key
    for key, rows in r.LRNI.items():
        for sex in rows:
            for lrni, rni in zip(rows[sex], r.RNI[key][sex]):
                assert lrni is None or lrni < rni
