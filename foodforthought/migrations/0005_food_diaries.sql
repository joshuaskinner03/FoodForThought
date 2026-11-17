CREATE TABLE food_diary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES client(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    start_date TEXT NOT NULL,
    days INTEGER NOT NULL DEFAULT 1,
    life_stage TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX food_diary_client ON food_diary(client_id, start_date);

CREATE TABLE diary_entry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    diary_id INTEGER NOT NULL REFERENCES food_diary(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    meal TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    food_id INTEGER REFERENCES food(id),
    grams REAL,
    quantity REAL,
    unit TEXT NOT NULL DEFAULT '',
    source_text TEXT NOT NULL DEFAULT '',
    source_amount TEXT NOT NULL DEFAULT '',
    source_detail TEXT NOT NULL DEFAULT '',
    app_kcal REAL,
    status TEXT NOT NULL DEFAULT 'confirmed',
    suggestions TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX diary_entry_diary ON diary_entry(diary_id, day, position);

CREATE TABLE food_match (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text_key TEXT NOT NULL UNIQUE,
    food_id INTEGER NOT NULL REFERENCES food(id),
    confirmed_count INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

ALTER TABLE attachment ADD COLUMN diary_id INTEGER REFERENCES food_diary(id) ON DELETE SET NULL;
