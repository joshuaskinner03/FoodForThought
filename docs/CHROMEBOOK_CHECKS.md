# Checks to do by hand on the Chromebook

Do these in order. Note anything that differs from "Expect".

## Phase 2, step 1: foods and targets

The importer was built from the published guide to the CoFID file and tested on
files made to that description, not on the real file. Steps 3 and 4 are the
ones that matter most.

1. **Swap the program folder.** In *Linux files*, delete the old
   `foodforthought` folder, extract the new zip and move its `foodforthought`
   folder in. Start as before:
   ```
   cd foodforthought
   bash start.sh
   ```
   Expect the one-off setup message (internet needed this once).
2. **Download the food tables.** Open Foods → National food database. Follow
   the link to gov.uk and download the Excel file "McCance and Widdowson's
   composition of foods integrated dataset" (about 4 MB).
3. **Import.** Choose that file and press Import. Note how long it takes. Then
   send me the report it shows, in particular:
   - Foods: expect roughly 2,900.
   - Nutrients: expect 45 of 45, or tell me which are listed as not found.
   - Energy check: expect nearly all foods to agree. If it says "That is too
     few", stop and tell me.
   - Sheets: the three lines, including "from row" and "matched by".
4. **Spot-check three foods.** Open the Excel file itself (the Files app can
   open it) and pick three foods. For each, compare energy, protein, calcium
   and vitamin C in the file with the food's page in the app. They must match.
5. **Search.** In Foods, type `milk semi`, then `bread wholemeal`. Expect the
   list to narrow while typing and sensible foods at the top.
6. **Weights and portions.** On a food page, type 250 in "Show for" and press
   Show: figures should scale. Add a portion ("1 glass", 200) and click it.
7. **A food of your own.** New food of my own → name, energy and a few values
   from any pack. Expect it marked "Mine" and listed first in a search.
8. **Targets.** Open a client with a sex, date of birth and a weight and height
   under Measurements → Targets → Work out new targets. Expect age, weight and
   height filled in. Save, and check the figures look reasonable.
9. **Backup.** Download a new encrypted backup. It will be a few MB larger
   because it now holds the food tables.

## Phase 1b

1. **Swap the program folder.** In *Linux files*, delete the old
   `foodforthought` folder. Extract the new zip and move its `foodforthought`
   folder into *Linux files*. The records are stored elsewhere and are not
   touched.
2. **Start.** In Terminal:
   ```
   cd foodforthought
   bash start.sh
   ```
   Expect the one-off setup message (it needs the internet this once), then the
   unlock page. After unlocking, the clients from before should still be there.
3. **Dates.** Open Clients → New client. Expect the date of birth box to show
   `dd/mm/yyyy`. Type 05/12/1990 and save: the record should say 5 Dec 1990.
   Press *Calendar* next to any date box: a calendar should open, and picking a
   day should fill the box as day/month/year.
4. **Practice details.** Settings → Edit practice details. Fill in the name,
   address and how to pay. Add a logo (PNG or JPEG).
5. **Measurements.** Open a client with a sex and date of birth → Measurements.
   Add a reading with weight and height, then a second with weight only, on
   different dates. Expect BMI on both rows and a weight chart and a BMI chart.
   Hover over a point: its date and value should appear.
6. **Body fat.** Add a reading with skinfolds and choose an equation. The list
   says which sites each equation needs. Expect a body fat percentage.
7. **Invoice.** Client → Invoices → New invoice. Add two lines, Save draft and
   check the total. Issue invoice. Download the PDF and open it: check the
   letterhead, logo, name, lines, total and how to pay.
8. **Payment and receipt.** Record a payment for part of the amount (expect
   "Part paid"), then the rest (expect "Paid"). Download the receipt PDF.
9. **Income summary.** Invoices → Income summary. Expect this month to show
   the amount invoiced and received.
10. **Letter.** Client → Letters → *Letter to GP*. Expect the names filled in
    and a warning if no GP consent is recorded. Save, then download the PDF.
11. **Export.** Client → Overview → *Export or erase this record* → Download
    record as a zip. Open `record.html` from the zip in Chrome and check it
    reads sensibly. Delete the zip afterwards.
12. **Erase.** Do this on a made-up client with an issued invoice. Type their
    name and erase. Expect the client gone from Clients, the invoice still
    under Invoices → All, and "Client N (erased)" in the activity log.
13. **Backup.** Download a new encrypted backup to the USB stick and discard
    the older backup files.

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
