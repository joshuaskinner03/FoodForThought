# Checks to do by hand on the Chromebook

Do these in order. Note anything that differs from "Expect".

## Phase 1a

1. **Put the new folder in place.** Extract the zip and move the
   `foodforthought` folder into *Linux files*.
2. **Start.** In Terminal:
   ```
   cd foodforthought
   bash start.sh
   ```
   Expect the one-off setup message again (it is a new folder), then
   `Open http://localhost:8765 in Chrome`.
3. **Existing data carried over.** Expect the unlock page, not setup, and the
   password from before should work. The old `larder` folder in *Linux files*
   can then be deleted.
4. **Clock.** Home shows "Today" with the right date. If it is a day out near
   midnight, tell me: the Linux clock is in a different time zone.
5. **Add a client.** Clients → New client. Expect the date of birth box to show
   day/month/year. Fill in Allergies. Expect the allergy note at the top of the
   record on every tab.
6. **Search.** Add a second client, then type part of a name in the search
   box. Expect the list to narrow while typing, without pressing Enter.
7. **Consent and files.** Record a consent. Add a photo taken on a phone.
   *View* should open it in a new tab. *Download* should save it to Downloads.
8. **Diary.** Add an appointment for a client. Add a repeating one (every
   week, 4 in total) and check the next weeks. Add one that overlaps and expect
   the overlap notice. Open an appointment and set it to Completed.
9. **Calendar export.** Diary → Download .ics file. Optional: import it into a
   test calendar and check the times are right and names appear as initials.
10. **Session note.** Open a client → Notes → New note → start from *Initial
    consultation*. Save. Expect it on the Notes tab and under "Latest note" on
    Overview.
11. **Build a form.** Templates and forms → New form. Add a short answer, a
    "Choose any" with three choices, and a heading. Move one up. Save.
12. **Print.** Preview and print → Print blank copy. Expect a clean page with
    no menu bar or buttons in the print preview.
13. **Fill in a form.** Client → Forms → choose the form → Start. Save, then
    edit an answer.
14. **Backup covers the records.** Download an encrypted backup. Add a
    throwaway client. Restore the backup. Expect the throwaway client to be
    gone and everything else present, including the photo.
15. **Auto-lock on a record.** Set auto-lock to 1 minute, open a client and
    wait. Expect the lock screen. Unlock and set it back to 10.
16. **Activity log.** Expect a line for each thing done above, with the client
    named but no clinical content in the Detail column.

## Phase 0 (already done once; repeat only after reinstalling)

1. A password under 12 characters is refused at setup.
2. A wrong password shows "Wrong password"; five in a row adds a wait.
3. After Ctrl+C and `bash start.sh`, expect the unlock page with nothing
   downloaded, also with Wi-Fi off.
4. The database is unreadable on disk:
   ```
   head -c 15 .local/share/foodforthought/records.db; echo
   ```
   Run that from a fresh Terminal window. Expect unreadable characters, not
   the words `SQLite format 3`.
5. From a phone on the same Wi-Fi, the Chromebook's IP address with `:8765`
   should not connect.
