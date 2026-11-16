CREATE TABLE food_source (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    attribution TEXT NOT NULL DEFAULT '',
    file_name TEXT NOT NULL DEFAULT '',
    file_sha256 TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL DEFAULT '',
    report TEXT NOT NULL DEFAULT '{}'
);

INSERT INTO food_source(key, name) VALUES('custom', 'My foods');

CREATE TABLE food (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES food_source(id),
    code TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    food_group TEXT NOT NULL DEFAULT '',
    nutrients TEXT NOT NULL DEFAULT '{}',
    flags TEXT NOT NULL DEFAULT '{}',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX food_source_code ON food(source_id, code);

CREATE INDEX food_name ON food(name);

CREATE TABLE portion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    food_id INTEGER NOT NULL REFERENCES food(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    grams REAL NOT NULL
);

CREATE INDEX portion_food ON portion(food_id);

CREATE TABLE target (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES client(id) ON DELETE CASCADE,
    set_on TEXT NOT NULL,
    sex TEXT NOT NULL,
    age INTEGER NOT NULL,
    weight_kg REAL NOT NULL,
    height_cm REAL NOT NULL,
    equation TEXT NOT NULL,
    bmr_kcal REAL NOT NULL,
    pal REAL NOT NULL,
    adjustment_kcal REAL NOT NULL DEFAULT 0,
    energy_kcal REAL NOT NULL,
    protein_basis TEXT NOT NULL,
    protein_value REAL NOT NULL,
    fat_percent REAL NOT NULL,
    protein_g REAL NOT NULL,
    fat_g REAL NOT NULL,
    carbohydrate_g REAL NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX target_client ON target(client_id, set_on)
