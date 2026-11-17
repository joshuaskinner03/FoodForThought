# FoodForThought

Free, open-source, local-only practice management for a private nutritionist.
One user, one device, no cloud.

**Status: Phase 2, step 2 of 3.** Working now: everything in Phase 1 (client
records, diary, notes, forms, consent, files, measurements, invoices, letters,
export and erasure, backup), the food database, energy and macronutrient
targets, and food diaries with intake analysis. Still to come: recipes and
meal plans (step 3), and optional local AI drafting (Phase 3).

## Run it

ChromeOS (Linux environment), Linux or macOS:

```
cd foodforthought
bash start.sh
```

Windows: double-click `start.bat`.

To update, stop the app (Ctrl+C), then:

```
cd foodforthought
git pull
bash start.sh
```

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
- **Foods:** the UK national food tables (CoFID 2021) are imported from the
  Excel file you download from gov.uk, never bundled. Search by any words in
  any order, see nutrients for any weight, save your own portions, and add
  foods of your own from pack labels.
- **Targets:** basal metabolic rate from Henry (2005) or Mifflin-St Jeor
  (1990), an activity factor, an adjustment in kcal, then protein, fat and
  carbohydrate targets. Each set is kept with the figures it was based on.
- **Food diaries:** typed in beside a photo of a paper diary, sent to the
  client as a spreadsheet template and imported back, or imported from a
  Cronometer export. Imported foods are matched to the food tables and each
  match is checked before the diary can be analysed. The analysis compares
  average daily intake with UK reference values (see below).
- **Dates** are typed and shown as day/month/year whatever language Chrome is
  set to.

Two note templates, one intake form and one GP letter template are created on
first use as starting points. Edit or hide them under *Templates and forms*.

### The food database import

*Foods → National food database* reads the Proximates, Inorganics and Vitamins
sheets of the CoFID workbook. It finds the columns by their codes (PROT, FAT,
CHO and so on) and falls back to the column titles. In the file, `N` means the
nutrient is present but the amount is not known: it is stored as "not known",
never as zero. `Tr` (trace) is stored as zero and labelled. Bracketed figures
are estimates and are marked with an asterisk.

After each import a report shows what was read. It includes a check that each
food's energy agrees with its protein, fat, carbohydrate and alcohol at 4, 9,
3.75 and 7 kcal per gram. If many foods fail that check, the columns were read
wrongly and the figures must not be used.

Importing again updates foods in place, so saved portions and anything that
refers to a food are kept. CoFID has no portion sizes, so portions are your
own. Source: McCance and Widdowson's The Composition of Foods Integrated
Dataset 2021, Public Health England, under the Open Government Licence v3.0.

### Food diaries and analysis

Three ways in, from a client's *Food diaries* tab:

- **Type in a diary.** Add a photo or scan of the paper diary; it stays beside
  the entry form. Type part of a food's name, press Enter to take the top
  result, type the weight or pick a saved portion, press Enter. The day and
  meal stay set for the next food.
- **Send a diary template.** Downloads a spreadsheet with the dates filled in,
  drop-down lists for meal and unit, and a page of instructions. It holds no
  client details. Import it when it comes back.
- **Import a Cronometer export.** In Cronometer: More → Account → Account
  Data → Export Data → Export Food & Recipe Entries, for the diary dates. Only
  the food names, amounts, meals and dates are used. Cronometer's own nutrient
  figures are ignored and the foods are matched to CoFID like any other import.

Each imported line is matched by its words against food names. The best
suggestions are listed with a match score; one is preselected only when the
score is 60% or more or the same words have been confirmed before. Weights are
taken from grams, kilograms, ounces and pounds as given, millilitres as grams
(flagged), a weight written on the pack ("1 x 30g bar"), or a saved portion
whose name contains the unit ("slice", "glass"). Anything else needs a weight
typed in, and that weight can be saved as a new portion for next time. Each
confirmed match is remembered, so the next diary with the same words is
matched straight away and can be confirmed in one go.

The analysis needs every line confirmed or skipped. It averages over the days
with food recorded, and lists any empty days. Fewer than 3 days gives a
warning.

- Energy, protein, fat, saturates, carbohydrate, total sugars, fibre and salt
  (sodium x 2.5). Fat and saturates are shown as a percentage of food energy
  (excluding alcohol), carbohydrate and sugars of total energy, as in the UK
  guidance. Compared with the client's latest targets, if set, and with the
  guidance: fat no more than 35% and saturates no more than 11% (from age 4),
  carbohydrate at least 50% (from age 2), fibre and salt by age, protein
  against the RNI (0.75 g per kg for adults when a weight is recorded).
- Vitamins and minerals against the RNI and LRNI for the client's sex and age
  on the first diary day, with the increases for pregnancy and breastfeeding
  set in the diary's details. Below the LRNI is shown as "low intake: below
  the LRNI", between the two as "below the RNI". A diary cannot show a
  deficiency, and the page says so.
- Where foods have no value for a nutrient, the average is a minimum and
  carries an asterisk. If those foods carry more than 20% of the energy, a low
  intake is not flagged for that nutrient.

Reference values: Public Health England, Government Dietary Recommendations
(2016) for RNIs, fibre and salt; Department of Health, Dietary Reference
Values (COMA, 1991) for LRNIs and the pregnancy and breastfeeding increments.
Thiamin, niacin and vitamin B6 LRNIs are scaled from the RNI by the COMA
ratios. LRNIs for calcium and phosphorus at 11 to 18 and magnesium at 11 to 14
are left out because the published figures could not be confirmed. CoFID
cannot separate free sugars from total sugars. Supplements are not counted.
The tables are in `foodforthought/reference.py` and the arithmetic is checked
in `tests/test_food_diaries.py`.

### Energy equations

- Henry (2005), weight and height version, kcal a day: men 18 to 29,
  14.4W + 3.13H + 113; men 30 to 59, 11.4W + 5.41H - 137; men 60 and over,
  11.4W + 5.41H - 256; women 18 to 29, 10.4W + 6.15H - 282; women 30 to 59,
  8.18W + 5.02H - 11.6; women 60 and over, 8.52W + 4.21H + 10.7. W is weight
  in kg, H is height in cm.
- Mifflin-St Jeor (1990): 10W + 6.25H - 5 x age, plus 5 for men or minus 161
  for women.

Both are for adults aged 18 and over. Energy target = basal metabolic rate x
activity factor + adjustment. Carbohydrate grams use 3.75 kcal per gram
(monosaccharide equivalents) to match CoFID. Checked against hand calculations
in `tests/test_targets.py`.

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
the same information as JSON, and their files. Food diaries are included,
with what the client wrote beside each matched food.

*Erase* removes the client and everything attached to them. Issued and voided
invoices are kept, with the name and address printed on them and their
payments, as financial records, and are no longer linked to a client. The
activity log keeps its entries for the erased record, which hold record numbers
only. Remembered food matches made only from this client's diaries are deleted;
matches also confirmed for another client are kept, as they hold no client
details. Backups made earlier still contain the record until they are replaced.
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
