CREATE TABLE measurement (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES client(id) ON DELETE CASCADE,
    measured_on TEXT NOT NULL,
    weight_kg REAL,
    height_cm REAL,
    waist_cm REAL,
    skinfolds TEXT NOT NULL DEFAULT '{}',
    body_fat_method TEXT NOT NULL DEFAULT '',
    body_fat_percent REAL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX measurement_client ON measurement(client_id, measured_on);

CREATE TABLE invoice (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT UNIQUE,
    client_id INTEGER REFERENCES client(id) ON DELETE SET NULL,
    bill_to_name TEXT NOT NULL DEFAULT '',
    bill_to_address TEXT NOT NULL DEFAULT '',
    issue_date TEXT NOT NULL,
    due_date TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    vat_bp INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX invoice_client ON invoice(client_id);

CREATE TABLE invoice_line (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoice(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    quantity TEXT NOT NULL DEFAULT '1',
    unit_pence INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX invoice_line_invoice ON invoice_line(invoice_id, position);

CREATE TABLE payment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoice(id) ON DELETE CASCADE,
    paid_on TEXT NOT NULL,
    amount_pence INTEGER NOT NULL,
    method TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX payment_invoice ON payment(invoice_id);

CREATE TABLE letter_template (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    recipient TEXT NOT NULL DEFAULT 'other',
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE letter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES client(id) ON DELETE CASCADE,
    letter_date TEXT NOT NULL,
    recipient_name TEXT NOT NULL DEFAULT '',
    recipient_address TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX letter_client ON letter(client_id, letter_date)
