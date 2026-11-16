# Phase 0: checks to do by hand on the Chromebook

Do these in order. Note anything that differs from "Expect".

1. **Linux is on.** Settings → About ChromeOS → Developers → Linux development
   environment. Turn it on if it is off.
2. **Put the folder in place.** Extract the zip into *Linux files* so the path
   is `Linux files/larder`.
3. **Start.** Open Terminal and run:
   ```
   cd ~/larder
   bash start.sh
   ```
   Expect: a setup message on the first run (it may install `python3-venv`),
   then `Open http://localhost:8765 in Chrome`. Note how long the first run
   takes.
4. **Browser.** Expect Chrome to open the page by itself. If it does not, open
   <http://localhost:8765> by hand and note that it did not open.
5. **Setup.** Try a password under 12 characters (expect a refusal), then set a
   real one. Expect the Home page showing `Encrypted (SQLCipher 4.x)`.
6. **Lock and unlock.** Click *Lock*. Enter a wrong password (expect "Wrong
   password"), then the right one. Note roughly how long unlocking takes; it
   should be under two seconds.
7. **Auto-lock.** Settings → set auto-lock to 1 minute. Leave the Chromebook
   untouched. Expect the page to switch to the lock screen within about 70
   seconds. Unlock, then keep typing in a password box for 90 seconds without
   submitting: expect it to stay unlocked.
8. **Backup to USB.** Home → *Download encrypted backup*. Expect a
   `.larderbackup` file in Downloads. Copy it to a USB stick with the Files app.
9. **Activity log.** Expect entries for setup, lock, unlock_failed, unlock,
   settings_changed and backup_created.
10. **Restore.** Settings → set auto-lock to 5 minutes. Then Settings →
    *Restore from a backup* → pick the file on the USB stick → enter the
    password. Expect the lock screen with "Backup restored". Unlock: auto-lock
    should be back to 1 minute, the activity log should end with a restore
    entry, and Settings should offer *Delete safety copy*. Delete it.
11. **Survives a restart.** Press Ctrl+C in Terminal, run `bash start.sh` again.
    Expect the unlock page (not setup) and it should start within a few seconds
    with no downloading. Repeat after restarting the Chromebook, with Wi-Fi off.
12. **Encrypted at rest.** In a second Terminal tab:
    ```
    head -c 15 ~/.local/share/larder/larder.db; echo
    ```
    Expect unreadable characters, not the words `SQLite format 3`.
13. **Not reachable from other devices.** From a phone on the same Wi-Fi, try
    the Chromebook's IP address with `:8765`. Expect no connection.
