# SnipesBot

A Slack bot for the Cornell Astronomical Society's `#snipes` channel. It reads
photo captions, classifies each one (snipe, snope, not-a-snipe, …), reacts with
a status emoji, keeps a leaderboard, and comes with an admin web UI for
reviewing and correcting what the bot decided.

```
Slack ──events──▶  bot (app.py)  ──▶  services  ──▶  SQLite (data/snipesbot.db)
                        ▲                               ▲        + archive files
                        └── background loop            │
                            (sync · review DMs)         │
                                                        │
Browser ──▶  admin webapp (webapp.py) ──▶ services ─────┘
```

Both processes share one database. SQLite in WAL mode with transactional
read-modify-write means they can write at the same time without losing each
other's changes.

## Quick start

```sh
python -m venv venv && . venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in the tokens

python -m snipesbot migrate     # ONLY if upgrading from the JSON version; see MIGRATING.md
python app.py                   # the Slack bot
python webapp.py                # the admin UI  →  http://127.0.0.1:8420
```

The webapp binds to loopback by default. To expose it, set `WEBAPP_PASSWORD`
(HTTP Basic, any username) and `WEBAPP_HOST`; it refuses to start exposed
without a password. Systemd units are in `deploy/`.

Slack app requirements are unchanged: Socket Mode, the bot scopes the old
version used (`channels:history`, `reactions:read/write`, `files:read`,
`chat:write`, `users:read`, `im:write`, `commands`), and the
`message`, `reaction_added` **and `reaction_removed`** event subscriptions
(the last is new: un-starring a photo now un-marks it as notable).

## Where the data lives

Everything is under one directory, `SNIPESBOT_DATA_DIR` (default `data`):

```
data/
  snipesbot.db            entries, messages, settings, do-not-snipe, scoring
  archive/files/          every photo/video, named by Slack file id (F0123ABC.jpg)
```

**The default `data` is relative to the directory you start the process from**,
so the bot, the webapp and CLI commands must all be started from the same
place (the systemd units set `WorkingDirectory`) or all given the same absolute
`SNIPESBOT_DATA_DIR`. Two different working directories means two different,
separate databases.

If photos show as missing, run `python -m snipesbot diagnose`. It prints the
absolute paths in use, how many photos the database can find, and examples of
the ones it can't, and it works even without network access. To reconnect
files that exist elsewhere on disk:

```sh
python -m snipesbot relink-files /path/to/old/data     # searches recursively
```

It matches files named by Slack file id (`F0123ABC.jpg`, the archive's naming)
or by entry id (the old `photos/` folder's naming). A photo whose file is gone
from your disk *and* older than Slack's retention window can't be recovered;
anything Slack still has is re-downloaded the first time you open it.

## How a photo is judged

Classification uses only the photo's own caption. Rules, in order:

| Caption | Result |
|---|---|
| contains a snope word | **snope** |
| `@mention` + a snipe word | **snipe** — counts toward the score |
| only `@mention`s, nothing else | **likely snipe** — needs confirming |
| `@mention` mixed with other text, or no mention | **unidentified** — needs a human |

Keywords match whole words, case-insensitively, and a negated keyword
(*"not a snipe"*, *"no snipe"*) doesn't count. The bot reacts with a matching
emoji: 🟢 snipe · 🟣 snope · 🟠 likely · ❓ unidentified · ⚫ not a snipe ·
🔴 missnipe · 🫆 special.

**Manual decisions always win.** Anything a person decides (by reacting as
Zeke, replying `#snipe @someone` in the thread, using the review DM, or using
the webapp) is locked: edits and sync never change it. Zeke's reactions:

| Reaction | Effect |
|---|---|
| 🟢 / 🟣 / ⚫ | set snipe / snope / not-a-snipe (not over a missnipe) |
| 🔴 | missnipe — only on a confirmed snipe |
| 🟠 | confirm a likely snipe as a snipe |
| ❓ | lock a pending photo as unidentified |
| 🫆 | send to the **Special** tab |
| ⭐ | notable (Zeke's star, or 3+ other people's; removable) |

## Admin UI

Live Log · Review Queue · Manual Decisions · Special · Search · Do Not Snipe ·
Analysis · Matrix · Adjustments · Maintenance.

Scoring is layered: the **Coded** matrix (what was classified) plus a
**Manual** matrix (hand corrections, can be negative) gives the **True**
matrix, from which Snipes/Sniped come. Score = Snipes − Sniped + a per-person
**Adjustment**.

## Sync, archive and maintenance

Slack's free tier hides old messages and files, so everything seen is copied
into a permanent local archive and read from there ever after (photos,
context and thread replies all come from it).

One job does all the upkeep (`snipesbot/services/sync.py`): archive new
messages → record any photo not yet tracked → re-run the rules on automatic
entries → notice photos deleted while the bot was offline. It runs every few
hours by itself, and on demand:

```sh
python -m snipesbot sync                       # continue from the last pass
python -m snipesbot sync --since 2026-09-01
python -m snipesbot sync --full                # rescan the whole season
python -m snipesbot sync --repair-reactions    # ask Slack about every reaction (slow)
python -m snipesbot export log.xlsx            # the spreadsheet is an export now
python -m snipesbot stats | diagnose | selftest | selftest-clear
```

The same tasks are in the webapp's **Maintenance** tab. A cross-process lock
means only one sync runs at a time, wherever it was started.

## Layout

```
snipesbot/
  config.py        settings, loaded explicitly (no import-time side effects)
  models.py        Kind enum, Entry dataclass
  classifier.py    pure caption → classification
  db.py            SQLite connection, transactions, schema migrations
  store/           data access: entries, archive (messages+files), small stores
  services/        entries (THE state-transition path), sync, archiver, dns,
                   notify, context, exports, legacy import, maintenance registry
  slack_app/       Bolt listeners → services; reactions policy table; review UI
  analysis/        Stats over one snapshot of the data
  web/             Flask app: auth, serializers, api/*; static ES modules
tests/             pytest suite + jsdom frontend smoke test
app.py  webapp.py  thin entry points (the old systemd units still work)
```

The one rule that keeps this tidy: **everything that changes a photo's
classification goes through `services.entries.apply_state`**. It updates the
row in one transaction, then fixes the Slack reaction and runs the
do-not-snipe check. Callers only decide *what* the new state is.

## Tests

```sh
pip install -r requirements-dev.txt
python -m pytest                  # ~185 tests, ~2s
sh tests/frontend/run.sh          # real frontend code in jsdom (needs Node 20+)
```

The suite uses a fake Slack client and a temp database. It covers the
classifier, store, every state transition, the sync job (including *how many
Slack calls it makes*), the Slack handlers, all web endpoints, scoring, the
importer, and the frontend's escaping of hostile Slack text.

## Configuration

See `.env.example`; every variable is documented there.
