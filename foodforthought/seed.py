from __future__ import annotations

import json

from .audit import now_iso
from .db import Database

NOTE_TEMPLATES = (
    (
        "Initial consultation",
        "Reason for visit:\n\n\nDiet history:\n\n\nLifestyle and activity:\n\n\n"
        "Assessment:\n\n\nPlan agreed:\n\n\nFollow-up:\n",
    ),
    (
        "Follow-up",
        "Progress since last session:\n\n\nChanges to diet or health:\n\n\n"
        "Discussion:\n\n\nPlan agreed:\n\n\nFollow-up:\n",
    ),
)

INTAKE_FIELDS = (
    ("heading", "About your visit", ()),
    ("textarea", "What would you like help with?", ()),
    ("textarea", "What have you already tried?", ()),
    ("heading", "Health", ()),
    ("textarea", "Medical conditions you have been diagnosed with", ()),
    ("textarea", "Medications and supplements you take", ()),
    ("textarea", "Food allergies or intolerances", ()),
    ("heading", "Eating", ()),
    (
        "multi",
        "Do you follow a particular way of eating?",
        ("No", "Vegetarian", "Vegan", "Gluten-free", "Dairy-free", "Other"),
    ),
    ("textarea", "What do you eat and drink on a typical weekday?", ()),
    ("number", "How many meals do you usually eat in a day?", ()),
    ("yesno", "Do you drink alcohol?", ()),
    ("textarea", "Anything else you would like me to know?", ()),
)


def apply(db: Database) -> None:
    if db.get_setting("seeded_core_admin"):
        return
    now = now_iso()
    with db.transaction():
        for name, body in NOTE_TEMPLATES:
            db.execute(
                "INSERT INTO note_template(name, body, created_at, updated_at) VALUES(?, ?, ?, ?)",
                (name, body, now, now),
            )
        fields = [
            {"id": f"q{i}", "type": kind, "label": label, "options": list(options), "required": False}
            for i, (kind, label, options) in enumerate(INTAKE_FIELDS, 1)
        ]
        db.execute(
            "INSERT INTO form_definition(name, kind, description, fields, created_at, updated_at) "
            "VALUES(?, 'intake', ?, ?, ?, ?)",
            ("New client intake", "A starting point. Edit it to suit your practice.", json.dumps(fields), now, now),
        )
        db.set_setting("seeded_core_admin", now)
