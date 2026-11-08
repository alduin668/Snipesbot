# Upgrading from the JSON version

Your existing data is imported once and the old files are **never modified**,
so you can roll back by checking out the old code.

## Steps

1. **Back up** `data/` (`cp -r data data.bak`).
2. Stop the bot and webapp.
3. Update the code and `pip install -r requirements.txt`.
4. Add `WEBAPP_PASSWORD` to `.env` (or keep `WEBAPP_HOST=127.0.0.1` and tunnel in).
   Remove `XLSX_PATH` if set; it's no longer used.
5. In the Slack app config, subscribe the bot to the **`reaction_removed`** event.
6. Import: `python -m snipesbot migrate`
   (`--from DIR` if the old files live elsewhere).
   Add `--recover-manuals` if you want the old "infer Zeke's manual decisions
   from his reactions" step (the old `migrate_manuals` script); it costs one
   Slack call per undecided photo.
   **Check the output for `NOT FOUND`.** If photo files weren't where the old
   index said they were (e.g. you copied `snipes.json` but not `archive/` and
   `photos/`, or the old index holds absolute paths from another machine) the
   import says so and lists where it looked. Copy the files over and run
   `python -m snipesbot relink-files <folder>`, or re-run `migrate` (it is
   idempotent and will pick up files it missed).
7. `python -m snipesbot diagnose` should show every photo found in the archive.
   Then `python -m snipesbot sync` to catch up (safe to skip; the bot does it on start).
8. Start the bot and webapp. Check the Live Log count matches what you expect;
   `python -m snipesbot stats` should show the leaderboard you had before.
9. Once you're happy, `snipes.json`, `photos/`, `archive/*.json` and
   `snipes_log.xlsx` can be archived or deleted. Photos now live only under
   `archive/files/`.

Verified: running the **old** code to produce real legacy files, then importing
them, gives an identical leaderboard to the old code's own.

## What the import does

* `ignored` entries become manual not-a-snipe (what the old recount did).
* "Malformed snipes" (a snipe with nobody sniped) become manual *unidentified*
  so they reappear in the Review Queue (what `fix_malformed_snipes` did). The
  new code can't create them.
* Photos that existed only in `photos/` are adopted into the archive.
* Settings, do-not-snipe list and its warnings, adjustments, manual matrix,
  names and test messages are carried over.
* Cached context, `status`, `ignored`, `photo_path` are dropped: context is
  read from the archive, `status` duplicated `kind`.

## Behaviour changes (all intentional)

**Fixes**
* **Deleting a snipe in Slack now removes it** from scoring and every queue,
  deletes the bot's do-not-snipe warning reply, and (by default; see
  `PURGE_FILES_ON_DELETE`) deletes the archived photo. Previously the entry
  kept counting and the photo stayed on disk. Deletions that happened while
  the bot was offline are noticed by the next sync.
* **Keywords match whole words and respect negation.** "not a snipe",
  "no snipe lol" and "sniper" are no longer auto-confirmed as snipes.
* Zeke's ❓ reaction works (it was dead code); un-starring a photo works
  (`reaction_removed`); a manual "notable" toggle is no longer overwritten by
  a later star.
* `#snipe Jonas` in a thread is no longer read as "nas". A thread reply that
  doesn't settle anything no longer locks the photo as a manual *unidentified*.
* The webapp no longer requires `SLACK_APP_TOKEN`.
* Stats and "time of day" now use one timezone (`TIMEZONE`, default the
  server's) instead of mixing local and UTC. Time buckets are
  morning/afternoon/evening/**night** everywhere (Analysis said "late-night").

**Less load on Slack, fewer surprises**
* The 6-hourly catch-up no longer makes a Slack call per photo. Reactions are
  only checked for photos whose reaction is known to be stale; use
  `sync --repair-reactions` for the old verify-everything behaviour.
* A failed reaction lookup no longer fans out into ~9 calls per photo;
  rate-limit responses are retried with backoff.
* Review DMs survive a restart (state is in the database, not in timers) and
  are only sent for live photos less than an hour old.
* Context shown for a photo now excludes thread replies (they appear under
  the photo) and "load more" has no ceiling.

**Security**
* The webapp binds to `127.0.0.1` by default and refuses to start exposed
  without `WEBAPP_PASSWORD`. Every write needs an `X-Requested-With` header
  (CSRF). The file proxy only serves files that were archived. All Slack text
  is escaped in the UI.

**Consolidation**
* The Scripts tab became **Maintenance**, with five tasks. `scripts/` is gone:

  | Old | New |
  |---|---|
  | `backfill`, `recount`, `archive_sync`, `resync` | `sync` (`--full`, `--since`) |
  | `migrate_manuals` | `migrate --recover-manuals` / Maintenance task (one-time) |
  | `fix_malformed_snipes` | done by `migrate`; can't recur |
  | `test_detector`, `cleartest_detector`, `/test` scripts | `selftest`, `selftest-clear` |
  | `diagnose`, `analysis` | `diagnose`, `stats` |
  | `write_reactions.sh`, `handlers/` | deleted (stale) |

* The `.xlsx` is generated on demand (Live Log → Download, or
  `python -m snipesbot export`), not kept in sync row by row.
* `recover_manuals` treats Zeke's 🫆 as *special* (its current meaning); the
  old script read it as a legacy not-a-snipe.
* Free-text names (e.g. "deer") are never DM'd or checked against do-not-snipe.
