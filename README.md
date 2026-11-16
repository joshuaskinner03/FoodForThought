# Larder

Free, open-source, local-only practice management for a private nutritionist.
One user, one device, no cloud. "Larder" is a working name.

**Status: Phase 0 (foundation).** This release has the encrypted store, password
and auto-lock, activity log, and encrypted backup and restore. Client records,
the diary, notes, invoices and nutrition analysis come in later phases.

## Run it

ChromeOS (Linux environment), Linux or macOS:

```
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
| ChromeOS / Linux | `~/.local/share/larder` |
| macOS | `~/Library/Application Support/larder` |
| Windows | `%LOCALAPPDATA%\larder` |

Set `LARDER_DATA` or pass `--data` to use another folder. Updating or replacing
the program folder never touches the data folder.

## Security model

- The password is stretched with Argon2id into a key that unwraps a random
  256-bit master key (`vault.json`). Changing the password re-wraps the master
  key; nothing else is re-encrypted.
- The database is SQLite encrypted with SQLCipher. Attachments are separate
  files, each encrypted with AES-256-GCM.
- The server listens on `127.0.0.1` only, checks the `Host` and `Origin`
  headers, and serves every script, style and font itself. It makes no outbound
  network requests.
- Locking closes the database and drops the keys. Auto-lock runs on the server,
  so it works even if the browser tab is closed.
- **There is no password reset.** A lost password means lost records.
- Not protected against: malware or another person using the unlocked
  ChromeOS session while the app is unlocked. Python cannot guarantee that key
  material is wiped from memory after locking.

## Backup and restore

*Download encrypted backup* produces one `.larderbackup` file holding the
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

AGPL-3.0-or-later. Food composition data is never bundled; it is imported from
files the user downloads.
