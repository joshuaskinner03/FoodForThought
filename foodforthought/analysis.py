from __future__ import annotations

import json
from datetime import date

from . import fmt, reference
from .nutrients import BY_KEY, KCAL_PER_GRAM, NUTRIENTS

MISSING_LIMIT = 0.2
MIN_DAYS = 3
STATES = {
    "below_lrni": "Low intake: below the LRNI",
    "below_rni": "Low intake: below the RNI",
    "meets": "At or above the RNI",
    "not_assessed": "Not assessed: too many foods have no value",
    "no_reference": "No reference value",
}


def _percent(part: float, whole: float) -> float | None:
    return part / whole * 100 if whole else None


def analyse(entries: list, declared_days: list[str], group: reference.Group | None, weight_kg: float | None, target) -> dict:
    days = sorted({entry["day"] for entry in entries})
    count = len(days)
    keys = [n.key for n in NUTRIENTS]
    totals = dict.fromkeys(keys, 0.0)
    gap_energy = dict.fromkeys(keys, 0.0)
    gap_count = dict.fromkeys(keys, 0)
    per_day = {day: {"entries": 0, **dict.fromkeys(("energy_kcal", "protein", "fat", "carbohydrate"), 0.0)} for day in days}
    total_energy = 0.0
    for entry in entries:
        nutrients = json.loads(entry["nutrients"]) if isinstance(entry["nutrients"], str) else entry["nutrients"]
        factor = entry["grams"] / 100
        energy = (nutrients.get("energy_kcal") or 0) * factor
        total_energy += energy
        per_day[entry["day"]]["entries"] += 1
        for key in keys:
            value = nutrients.get(key)
            if value is None:
                gap_energy[key] += energy
                gap_count[key] += 1
                continue
            totals[key] += value * factor
            if key in per_day[entry["day"]]:
                per_day[entry["day"]][key] += value * factor
    average = {key: totals[key] / count if count else 0.0 for key in keys}
    gap_share = {key: (gap_energy[key] / total_energy if total_energy else (1.0 if gap_count[key] else 0.0)) for key in keys}

    energy = average["energy_kcal"]
    food_energy = energy - average["alcohol"] * KCAL_PER_GRAM["alcohol"]
    salt = average["sodium"] * 2.5 / 1000

    def macro(key: str, label: str, value: float, unit: str, share: float | None, guidance: str = "", state: str = "", target_value=None) -> dict:
        return {
            "key": key,
            "label": label,
            "value": value,
            "unit": unit,
            "share": share,
            "guidance": guidance,
            "state": state,
            "gap_count": gap_count.get(key, 0),
            "gap_share": gap_share.get(key, 0.0),
            "target": target_value,
        }

    macros = [
        macro("energy_kcal", "Energy", energy, "kcal", None, target_value=target["energy_kcal"] if target else None),
    ]
    protein_share = _percent(average["protein"] * KCAL_PER_GRAM["protein"], energy)
    if group:
        rni, basis = reference.protein_rni(group, weight_kg)
        state = "below" if average["protein"] < rni else "within"
        macros.append(macro("protein", "Protein", average["protein"], "g", protein_share, f"RNI {rni:.1f} g ({basis})", state, target["protein_g"] if target else None))
    else:
        macros.append(macro("protein", "Protein", average["protein"], "g", protein_share, target_value=target["protein_g"] if target else None))

    fat_share = _percent(average["fat"] * KCAL_PER_GRAM["fat"], food_energy)
    saturates_share = _percent(average["saturates"] * KCAL_PER_GRAM["fat"], food_energy)
    carbohydrate_share = _percent(average["carbohydrate"] * KCAL_PER_GRAM["carbohydrate"], energy)
    sugars_share = _percent(average["sugars"] * KCAL_PER_GRAM["carbohydrate"], energy)
    grown = group is not None and group.age >= 4
    macros.append(
        macro(
            "fat", "Fat", average["fat"], "g", fat_share,
            "No more than 35% of food energy" if grown else "",
            ("above" if fat_share and fat_share > 35 else "within") if grown and fat_share is not None else "",
            target["fat_g"] if target else None,
        )
    )
    macros.append(
        macro(
            "saturates", "of which saturates", average["saturates"], "g", saturates_share,
            "No more than 11% of food energy" if grown else "",
            ("above" if saturates_share and saturates_share > 11 else "within") if grown and saturates_share is not None else "",
        )
    )
    carbs_apply = group is not None and group.age >= 2
    macros.append(
        macro(
            "carbohydrate", "Carbohydrate", average["carbohydrate"], "g", carbohydrate_share,
            "At least 50% of total energy" if carbs_apply else "",
            ("below" if carbohydrate_share is not None and carbohydrate_share < 50 else "within") if carbs_apply and carbohydrate_share is not None else "",
            target["carbohydrate_g"] if target else None,
        )
    )
    macros.append(
        macro(
            "sugars", "of which total sugars", average["sugars"], "g", sugars_share,
            "No recommendation for total sugars. Free sugars should be no more than 5% of energy, but the food tables do not separate them.",
        )
    )
    fibre_goal = reference.fibre_g(group.age) if group else None
    macros.append(
        macro(
            "fibre", "Fibre (AOAC)", average["fibre"], "g", None,
            f"{fibre_goal:g} g a day" if fibre_goal else "",
            ("below" if average["fibre"] < fibre_goal else "within") if fibre_goal else "",
        )
    )
    if average["alcohol"] > 0:
        macros.append(macro("alcohol", "Alcohol", average["alcohol"], "g", _percent(average["alcohol"] * KCAL_PER_GRAM["alcohol"], energy)))
    salt_max = reference.salt_max_g(group.age) if group else None
    macros.append(
        {
            **macro("sodium", "Salt", salt, "g", None, f"No more than {salt_max:g} g a day" if salt_max else "", ("above" if salt > salt_max else "within") if salt_max else ""),
            "detail": f"From {average['sodium']:.0f} mg sodium (salt = sodium x 2.5)",
        }
    )

    micros = []
    references = reference.values(group) if group else {}
    for key in reference.ORDER:
        nutrient = BY_KEY[key]
        value = average[key]
        ref = references.get(key)
        row = {
            "key": key,
            "label": nutrient.label,
            "unit": nutrient.unit,
            "places": nutrient.places,
            "value": value,
            "lrni": ref["lrni"] if ref else None,
            "rni": ref["rni"] if ref else None,
            "raised": ref["raised"] if ref else False,
            "percent_rni": _percent(value, ref["rni"]) if ref else None,
            "gap_count": gap_count[key],
            "gap_share": gap_share[key],
        }
        if not ref:
            row["state"] = "no_reference"
        elif value >= ref["rni"]:
            row["state"] = "meets"
        elif gap_share[key] > MISSING_LIMIT:
            row["state"] = "not_assessed"
        elif ref["lrni"] is not None and value < ref["lrni"]:
            row["state"] = "below_lrni"
        else:
            row["state"] = "below_rni"
        row["state_label"] = STATES[row["state"]]
        micros.append(row)

    empty_days = [day for day in declared_days if day not in per_day]
    notes = []
    if group and group.life_stage.startswith("pregnant"):
        notes.append(
            "Government advice in pregnancy is a 400 µg folic acid supplement daily until the 12th week. "
            "Supplements are not included in these figures."
        )
    if group and group.sex == "female" and 11 <= group.age < 50:
        notes.append("The iron RNI may not be enough for women with heavy periods.")
    notes.append(
        "Few foods contain vitamin D. Government advice is that everyone should consider a 10 µg vitamin D "
        "supplement daily, at least in autumn and winter."
    )
    return {
        "days": days,
        "day_count": count,
        "too_few_days": count < MIN_DAYS,
        "empty_days": empty_days,
        "entries": len(entries),
        "average": average,
        "macros": macros,
        "micros": micros,
        "per_day": [{"day": day, **per_day[day]} for day in days],
        "group": group,
        "notes": notes,
        "missing_limit": int(MISSING_LIMIT * 100),
    }


def age_on(date_of_birth: str, when: str) -> int | None:
    return fmt.age(date_of_birth, fmt.parse_date(when) or date.today())
