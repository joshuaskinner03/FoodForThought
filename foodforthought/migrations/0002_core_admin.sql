ALTER TABLE audit_log ADD COLUMN client_id INTEGER;

CREATE TABLE client (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    preferred_name TEXT NOT NULL DEFAULT '',
    date_of_birth TEXT NOT NULL DEFAULT '',
    sex TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    gp_name TEXT NOT NULL DEFAULT '',
    gp_address TEXT NOT NULL DEFAULT '',
    medical_history TEXT NOT NULL DEFAULT '',
    allergies TEXT NOT NULL DEFAULT '',
    medications TEXT NOT NULL DEFAULT '',
    goals TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX client_name ON client(last_name, first_name);

CREATE TABLE consent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES client(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    given_on TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    withdrawn_on TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX consent_client ON consent(client_id);

CREATE TABLE attachment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES client(id) ON DELETE CASCADE,
    file_id TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX attachment_client ON attachment(client_id);

CREATE TABLE appointment_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repeat TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE appointment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER REFERENCES client(id) ON DELETE CASCADE,
    series_id INTEGER REFERENCES appointment_series(id) ON DELETE SET NULL,
    title TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'booked',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX appointment_date ON appointment(date, start_time);

CREATE INDEX appointment_client ON appointment(client_id);

CREATE TABLE note_template (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE note (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES client(id) ON DELETE CASCADE,
    appointment_id INTEGER REFERENCES appointment(id) ON DELETE SET NULL,
    note_date TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX note_client ON note(client_id, note_date);

CREATE TABLE form_definition (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'intake',
    description TEXT NOT NULL DEFAULT '',
    fields TEXT NOT NULL DEFAULT '[]',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE form_response (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES client(id) ON DELETE CASCADE,
    form_id INTEGER REFERENCES form_definition(id) ON DELETE SET NULL,
    form_name TEXT NOT NULL,
    fields TEXT NOT NULL,
    answers TEXT NOT NULL DEFAULT '{}',
    completed_on TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX form_response_client ON form_response(client_id)
