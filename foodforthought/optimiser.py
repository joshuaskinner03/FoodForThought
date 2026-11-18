from __future__ import annotations

from math import sqrt

GOALS = (
    ("energy_kcal", "energy_kcal", "Energy", "kcal", 2.0),
    ("protein", "protein_g", "Protein", "g", 1.5),
    ("fat", "fat_g", "Fat", "g", 1.0),
    ("carbohydrate", "carbohydrate_g", "Carbohydrate", "g", 1.0),
)
ROOM = {"25": (0.75, 1.25), "50": (0.5, 1.5), "100": (0.5, 2.0)}
ROOM_LABELS = {"25": "by up to a quarter", "50": "by up to a half", "100": "from half to double"}
STEADY = 0.05
SWEEPS = 1000


def solve(supply: list[list[float]], targets: list[float], weights: list[float], low: float, high: float, fixed: list[bool]) -> list[float]:
    count, goals = len(supply), len(targets)
    rows = [[supply[i][k] / targets[k] * sqrt(weights[k]) for i in range(count)] for k in range(goals)]
    wanted = [sqrt(weights[k]) for k in range(goals)]
    scales = [1.0] * count
    weight = [sum(rows[k][i] ** 2 for k in range(goals)) for i in range(count)]
    residual = [sum(rows[k][i] for i in range(count)) - wanted[k] for k in range(goals)]
    for _ in range(SWEEPS):
        moved = 0.0
        for i in range(count):
            if fixed[i] or weight[i] == 0:
                continue
            others = sum(rows[k][i] * (residual[k] - rows[k][i] * scales[i]) for k in range(goals))
            best = min(high, max(low, (STEADY - others) / (weight[i] + STEADY)))
            step = best - scales[i]
            if step:
                for k in range(goals):
                    residual[k] += rows[k][i] * step
                scales[i] = best
                moved = max(moved, abs(step))
        if moved < 1e-9:
            break
    return scales


def rounded(item: dict, scale: float) -> dict:
    if item["kind"] == "recipe":
        servings = max(0.25, round(item["servings"] * scale * 4) / 4)
        return {"servings": servings, "factor": servings / item["servings"]}
    if item["quantity"]:
        quantity = max(0.5, round(item["quantity"] * scale * 2) / 2)
        each = item["grams"] / item["quantity"]
        return {"quantity": quantity, "grams": round(quantity * each, 1), "factor": quantity / item["quantity"]}
    grams = item["grams"] * scale
    grams = max(1.0, round(grams / 5) * 5 if grams >= 20 else round(grams))
    return {"grams": grams, "factor": grams / item["grams"]}


def suggest(items: list[dict], supply: list[dict], target, room: str, keep: set[int]) -> dict:
    low, high = ROOM.get(room, ROOM["50"])
    goals = [goal for goal in GOALS if target[goal[1]]]
    matrix = [[values[key] for key, *_ in goals] for values in supply]
    scales = solve(matrix, [target[g[1]] for g in goals], [g[4] for g in goals], low, high, [item["id"] in keep for item in items])
    changes = []
    for item, scale in zip(items, scales):
        change = rounded(item, scale) if item["id"] not in keep else {"factor": 1.0}
        changes.append(dict(change, item=item, kept=item["id"] in keep, changed=abs(change["factor"] - 1) > 1e-9))
    totals = []
    for key, target_key, label, unit, _ in GOALS:
        before = sum(values[key] for values in supply)
        after = sum(values[key] * change["factor"] for values, change in zip(supply, changes))
        goal = target[target_key]
        totals.append({"label": label, "unit": unit, "target": goal, "before": before, "after": after,
                       "before_share": before / goal * 100 if goal else None, "after_share": after / goal * 100 if goal else None})
    return {"changes": changes, "totals": totals}
