CREATE TABLE recipe (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    servings REAL NOT NULL DEFAULT 1,
    cooked_grams REAL,
    tags TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE recipe_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    food_id INTEGER NOT NULL REFERENCES food(id),
    grams REAL NOT NULL,
    quantity REAL,
    unit TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT ''
);

CREATE INDEX recipe_item_recipe ON recipe_item(recipe_id, position);

CREATE TABLE meal_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER REFERENCES client(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    days INTEGER NOT NULL,
    start_date TEXT,
    intro TEXT NOT NULL DEFAULT '',
    show_figures INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX meal_plan_client ON meal_plan(client_id);

CREATE TABLE meal_plan_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES meal_plan(id) ON DELETE CASCADE,
    day INTEGER NOT NULL,
    meal TEXT NOT NULL,
    position INTEGER NOT NULL,
    recipe_id INTEGER REFERENCES recipe(id),
    food_id INTEGER REFERENCES food(id),
    servings REAL,
    grams REAL,
    quantity REAL,
    unit TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    CHECK ((recipe_id IS NULL) <> (food_id IS NULL))
);

CREATE INDEX meal_plan_item_plan ON meal_plan_item(plan_id, day, position);
