# FoodForThought

Free, open-source, local-only practice management for a private nutritionist.
One user, one device, no cloud.

**Status: Phase 1 complete.** Working now: client records, appointment diary,
session notes, custom forms, consent records, encrypted file attachments,
measurements with progress charts, invoices and receipts as PDF, an income
summary, letters as PDF, per-client export and erasure, activity log, password
and auto-lock, encrypted backup and restore. Still to come: nutrition analysis
(Phase 2) and optional local AI drafting (Phase 3).

## Run it

ChromeOS (Linux environment), Linux or macOS:

```
cd foodforthought
bash start.sh
```

Windows: double-click `start.bat`.

The first run creates a private Python environment in `.venv` and downloads the
dependencies. After that it starts offline. The app opens at
<http://localhost:8765>. Stop it with Ctrl+C in the terminal.

Options: `--port 8765`, `--data /path/to/folder`, `--no-browser`.

## Where the data lives

| System | Folder |
| --- | --- |
| ChromeOS / Linux | `~/.local/share/foodforthought` |
| macOS | `~/Library/Application Support/foodforthought` |
| Windows | `%LOCALAPPDATA%\foodforthought` |

Set `FOODFORTHOUGHT_DATA` or pass `--data` to use another folder. Updating or
replacing the program folder never touches the data folder.

The app was first called Larder. A data folder left by that version is moved
to the new name on first start, and backups it made (`.larderbackup`) still
restore.

## What it does

- **Clients:** contact details, GP, medical history, allergies, medications and
  goals. Allergies show at the top of every page of the record. Clients are
  archived, or erased for good from *Export or erase this record*.
- **Consent and files:** dated consent records that can be withdrawn. Files of
  up to 25 MB are stored encrypted. Images open in the browser, everything else
  downloads.
- **Diary:** week view, repeating appointments (weekly, 2-weekly, 4-weekly,
  monthly, up to 52), a notice when appointments overlap, and export to an
  `.ics` calendar file with initials by default.
- **Notes:** session notes per client, started from a blank page or a
  template.
- **Forms:** build intake and assessment forms from eight answer types, print
  a blank copy, or fill one in on a client's record. A completed form keeps the
  questions it was filled in with.
- **Measurements:** weight, height, waist and skinfolds in metric units. BMI is
  worked out from the latest height. Body fat is estimated from skinfolds (see
  below). A chart appears for each measure with two or more readings.
- **Invoices:** drafts become numbered invoices when issued and cannot be
  edited afterwards, only voided. Payments are recorded against them. Invoice
  and receipt are PDFs with your letterhead. VAT is off until you tick *VAT
  registered* in practice details. The income summary follows the UK tax year,
  6 April to 5 April.
- **Letters:** templates with fill-in fields such as `[Client name]`, saved per
  client and exported as PDF. Starting a letter to a GP warns if no consent to
  share with the GP is recorded.
- **Dates** are typed and shown as day/month/year whatever language Chrome is
  set to.

Two note templates, one intake form and one GP letter template are created on
first use as starting points. Edit or hide them under *Templates and forms*.

### Body fat equations

Skinfolds are turned into body density, then into body fat with the Siri
equation, body fat % = (4.95 / density - 4.50) x 100.

- Durnin and Womersley (1974), biceps, triceps, subscapular and suprailiac,
  with separate coefficients by sex and age band.
- Jackson and Pollock (1978) for men and Jackson, Pollock and Ward (1980) for
  women: 3 sites (men chest, abdomen, thigh; women triceps, suprailiac, thigh)
  and 7 sites.

The coefficients are in `foodforthought/measurements.py` and checked against
hand calculations in `tests/test_measurements.py`. These are estimates with a
wide margin of error, developed on adults.

### Export and erasure

*Export* makes an unencrypted zip of one client's whole record: a page to read,
the same information as JSON, and their files.

*Erase* removes the client and everything attached to them. Issued and voided
invoices are kept, with the name and address printed on them and their
payments, as financial records, and are no longer linked to a client. The
activity log keeps its entries for the erased record, which hold record numbers
only. Backups made earlier still contain the record until they are replaced.
How long financial records must be kept is for the practitioner to decide with
their accountant.

## Security model

- The password is stretched with Argon2id into a key that unwraps a random
  256-bit master key (`vault.json`). Changing the password re-wraps the master
  key and re-encrypts nothing else.
- The database is SQLite encrypted with SQLCipher. Attachments are separate
  files, each encrypted with AES-256-GCM.
- The server listens on `127.0.0.1` only, checks the `Host` and `Origin`
  headers, and serves every script and style itself. It makes no outbound
  network requests.
- Locking closes the database and drops the keys. Auto-lock runs on the server,
  so it works even if the browser tab is closed.
- The activity log records what was done and to which record, never the
  content.
- **There is no password reset.** A lost password means lost records.
- Not protected against: malware, or another person using the Chromebook while
  the app is unlocked. Python cannot guarantee that key material is wiped from
  memory after locking.
- Anything exported is no longer protected: a downloaded attachment, an `.ics`
  file, a PDF, a record export, a printed form.

## Backup and restore

*Download encrypted backup* produces one `.fftbackup` file holding the
database, the key vault and the attachments, all still encrypted, plus a signed
manifest. It opens only with the password in use when it was made.

Restoring checks the password, the signature and every file before anything is
replaced. The data being replaced is kept as a safety copy in the data folder
until it is deleted in Settings.

The ChromeOS Linux environment is wiped by a Powerwash and is not part of the
normal ChromeOS sync, so the backup file is the only copy. Keep it on a USB
stick.

## Development

```
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
```

Manual checks for the Chromebook are in `docs/CHROMEBOOK_CHECKS.md`.

## Licence

AGPL-3.0-or-later. Food composition data is never bundled, only imported from
files the user downloads. Included third-party files: htmx (`static/vendor`,
Zero-Clause BSD) and the DejaVu Sans fonts used in PDFs (`fonts`, Bitstream
Vera and public domain terms).
