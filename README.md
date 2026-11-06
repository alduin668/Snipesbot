# SnipesBot

Slack bot for the Cornell Astronomical Society's `#snipes` channel. Members
post candid ("sniped") photos of each other; the bot classifies each photo
from its caption, reacts with a color-coded status emoji, and keeps a
running log and leaderboard. A companion webapp gives Zeke a browser-based
admin surface for everything the bot can't (or shouldn't) infer on its own.

## Project layout

```
snipesbot/
├── app.py                    # Slack bot entrypoint (Socket Mode)
├── webapp.py                  # Webapp entrypoint (thin wrapper around web/app.py)
├── config.py                   # All configuration/env vars, no side effects
├── bot/                          # Slack-facing logic
│   ├── detection.py                # Classification engine + per-photo entry lifecycle (the core state machine)
│   ├── reactions.py                 # Reaction shortcuts (green/purple/orange/black/red/star)
│   ├── review.py                     # /reviewsnipes, /reviewmode, DM + modal flow
│   ├── blocking.py                    # /donotsnipe + warning cleanup
│   ├── catchup.py                      # Background auto catch-up loop
│   └── testing.py                       # /test, /cleartest -- synthetic detector test battery
├── storage/                    # All persistence -- nothing above this layer touches disk directly
│   ├── json_store.py              # snipes.json, do-not-snipe list, settings, adjustments, manual matrix
│   ├── xlsx_store.py               # The .xlsx "true log"
│   └── user_names.py                # Slack user id -> display name cache
├── analysis/
│   └── stats.py                  # Coded/True matrices, leaderboard/scoring, streaks/rivalries/etc.
├── scripts/                    # One-off maintenance scripts, run as `python -m scripts.X`
│   ├── backfill.py                 # Scan Slack history + reconcile (shared core w/ catchup.py)
│   ├── recount.py                   # Re-run current rules over all history
│   ├── resync.py                     # Rebuild the .xlsx from snipes.json
│   ├── migrate_manuals.py             # One-time recovery of pre-manually_resolved decisions
│   ├── fix_malformed_snipes.py         # Demotes "snipe" entries with no sniped user back to unidentified
│   ├── test_detector.py                # Standalone runner for the /test battery
│   ├── cleartest_detector.py            # Standalone runner for /cleartest
│   └── diagnose.py                       # Slack token/scope connectivity check
├── web/                         # The live-log/admin webapp (Flask)
│   ├── app.py                      # App factory
│   ├── routes.py                    # All /api/* routes + the index page
│   ├── scripts_runner.py             # Runs scripts/*.py as subprocesses for the Scripts tab
│   ├── templates/index.html           # Page shell (tabs: Live Log, Review Queue, Manual Decisions,
│   │                                    Search, Do Not Snipe, Analysis, Matrix, Adjustments, Scripts)
│   └── static/{style.css,app.js}       # CSS + all client-side JS
├── deploy/
│   ├── snipesbot.service          # systemd unit for the bot
│   └── webapp.service              # systemd unit for the webapp
├── .env.example
└── requirements.txt
```

Two independent processes read/write the same `data/` directory:

- `app.py` -- the actual Slack bot (Socket Mode). Needs `SLACK_APP_TOKEN`.
- `webapp.py` -- the Live Log / Review Queue / Analysis / Scripts dashboard. Only needs `SLACK_BOT_TOKEN`.

They coordinate purely through the files in `data/` (written atomically by
`storage/json_store.py` and `storage/xlsx_store.py`), so it's safe to run
both at once. See `web/app.py`'s docstring for the "no auth on the webapp"
security note before exposing it beyond a trusted LAN.

### Data files

Everything under `data/` (paths configurable in `config.py`), all written
atomically (temp file + `os.replace`):

| File                 | Written by               | What it holds                                                                             |
|----------------------|--------------------------|-------------------------------------------------------------------------------------------|
| `snipes.json`        | the bot                  | Every photo entry ever seen -- the canonical per-photo log                                |
| `manual_matrix.json` | webapp (Matrix tab)      | Zeke's cell-by-cell corrections/additions on top of the coded matrix -- see Scoring below |
| `adjustments.json`   | webapp (Adjustments tab) | Manual per-person score adjustments                                                       |
| `do_not_snipe.json`  | `/donotsnipe`, webapp    | Do-not-snipe consent list                                                                 |
| `dns_warnings.json`  | the bot                  | Outstanding DNS warning messages, for auto-cleanup on delete                              |
| `settings.json`      | the bot                  | Small key/value settings (review-DM toggle, catch-up watermark)                           |
| `user_names.json`    | the bot                  | Slack user id -> display name cache                                                       |
| `test_messages.json` | `/test`                  | What the last test battery posted, for `/cleartest` to find                               |
| `snipes_log.xlsx`    | webapp/scripts           | The "true log" workbook (mirrors `snipes.json`, see `xlsx_store.py`)                      |
| `photos/`            | the bot                  | Downloaded copies of each photo                                                           |

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your tokens/IDs
python app.py           # the bot
python webapp.py        # the dashboard, in a second terminal/service
```

Key environment variables (see `config.py` for the full list and defaults):

| Variable                        | Required       | Purpose                                                                                             |
|---------------------------------|----------------|-----------------------------------------------------------------------------------------------------|
| `SLACK_BOT_TOKEN`               | yes            | `xoxb-...` bot token, used by both processes                                                        |
| `SLACK_APP_TOKEN`               | yes (bot only) | `xapp-...` Socket Mode token                                                                        |
| `SNIPES_CHANNEL_ID`             | yes            | Channel **ID** (not name) of `#snipes`                                                              |
| `ZEKE_USER_ID`                  | yes            | Zeke's Slack user ID -- the only user whose reactions/thread replies carry classification authority |
| `REVIEWER_USER_ID`              | no             | Who gets DM'd unidentified photos in review mode; defaults to `ZEKE_USER_ID`                        |
| `WEBAPP_HOST` / `WEBAPP_PORT`   | no             | Default `0.0.0.0:8420`                                                                              |
| `CONTEXT_CAPTURE_DELAY_SECONDS` | no             | How long to wait for after-context before finalizing a pending photo (default 90s)                  |
| `SNIPESBOT_DATA_DIR`            | no             | Default `data/`                                                                                     |
| `XLSX_PATH`                     | no             | Default `<data_dir>/snipes_log.xlsx`                                                                |
| `SEASON_START_DATE`             | no             | First catch-up/recount scan boundary (default `2026-08-15`)                                         |
| `CATCHUP_INTERVAL_SECONDS`      | no             | Background catch-up cadence (default 6h)                                                            |

## Classification

Every image is classified independently from **that photo message's own
caption**. Neighboring messages are stored only as review context for a
human -- they never affect automatic classification.

| State             | Reaction | Meaning                                                                              |
|-------------------|----------|--------------------------------------------------------------------------------------|
| `unidentified`    | ❓        | No usable evidence; needs human review                                               |
| `likely_snipe`    | 🟠       | Bare @mention; probably a snipe, but unconfirmed                                     |
| `snipe`           | 🟢       | Confirmed snipe                                                                      |
| `snope`           | 🟣       | Joke/fake snipe                                                                      |
| `not_a_snipe`     | ⚫        | Human-confirmed NAS                                                                  |
| `incorrect_snipe` | 🔴       | Zeke says a confirmed snipe was incorrect (no automatic score effect -- see Scoring) |

Legacy `:fingerprint:`/`:thumbsup:` decisions are migrated to black NAS
during recount.

### Detection rules

Applied in this order, using only the caption text:

1. A snope keyword always wins (with or without an @mention).
2. A caption containing an @mention plus a snipe keyword becomes `snipe`.
3. A caption containing only one or more @mentions (nothing else) becomes `likely_snipe`.
4. An @mention mixed with ordinary text but no snipe keyword is `unidentified`.
5. A caption with no usable @mention is `unidentified`.

Keywords live in `config.py`:

```python
SNOPE_KEYWORDS = {"snope", "snoped", "snopes", "snoping"}
SNIPE_KEYWORDS = {"snipe", "sniped", "snipes", "sniping"}
```

An edit to a photo's caption (Slack's `message_changed`) is re-classified
the same way, unless the entry is already `manually_resolved` -- in that
case only the stored caption text is refreshed, the classification itself
never changes.

### Manual decisions

Any explicit Zeke classification is stored with `"manually_resolved": true`
and is permanent -- `scripts/recount.py` will never reclassify it. There
are four ways to make one:

- **Slack reactions** on the photo message (`bot/reactions.py`) -- green/ purple/black/red/orange/star, restricted to Zeke. A red reaction is deliberately narrow: it only changes a confirmed `snipe` to `incorrect_snipe`; it never means "unidentified."
- **A thread reply containing** `#snipe` on the photo message (`app.py` + `detection.resolve_via_thread_reply`). If the reply contains no @mention and the word "nas", the photo is resolved `not_a_snipe`. Otherwise the reply's text is appended to the original caption and re-run through the normal classifier -- so `#snipe @someone` in a thread reply works exactly like putting `snipe @someone` in the caption itself. This only applies to still-`unidentified`/`likely_snipe` photos.
- `/reviewsnipes` in Slack, or the webapp's **Review Queue** / **Manual Decisions** tabs -- buttons that call the same underlying `resolve_entry`/`mark_manual_kind` functions reactions use, but work on *any* entry regardless of its current state (e.g. "Missnipe" can be applied from any kind, not just a confirmed snipe -- the webapp is the admin surface, so nothing is off-limits there the way a narrow reaction is).
- `scripts/migrate_manuals.py`, for historical entries predating `manually_resolved` (see Maintenance scripts below).

## Review queue

`/reviewsnipes` shows the oldest pending (`unidentified` or `likely_snipe`)
photo. When `/reviewmode on` is set, a genuinely unidentified ("?") photo
also gets DM'd to `REVIEWER_USER_ID` (defaults to Zeke) as soon as its
context-capture window closes -- `likely_snipe` photos are lower-urgency
(they already have a guessed @mention) and don't interrupt anyone.

Resolution actions -- available via Slack buttons, a thread reply, or the
webapp's Review Queue / Manual Decisions tabs -- are: *It's a snipe*,
*It's a snope*, *Not a snipe*, *Skip for now* (Slack modal flow), or any of
the six state buttons (webapp).

**Context capture** (`bot/detection.py`) collects up to `CONTEXT_WINDOW`
messages immediately before and after a pending photo as a hint for
whoever reviews it -- purely informational, never affects classification.
This state lives in memory for the life of the bot process: if the bot
restarts while a photo is still waiting on its after-context window, that
particular timer is lost and the photo won't auto-trigger a reviewer DM
(though it still shows up normally in `/reviewsnipes` and the webapp, which
lazily backfills context the first time the entry's card is opened).

## Do Not Snipe

`/donotsnipe` (or the webapp's **Do Not Snipe** tab) adds a user to a
consent list. Zeke can add anyone by mention, cached display name, or a
live Slack lookup; anyone else can only add themselves. Whenever a `snipe`
of a listed user is recorded (live, via backfill, via recount, or via any
manual resolution), the bot publicly threads a "that's a sNOPE" reply and
DMs the sniper asking them to delete it. Warnings are tracked so they can
be auto-cleaned if the offending message is deleted.

## Notable photos

A photo becomes "notable" (⭐, shown highlighted in the Live Log) if Zeke
stars it, or if at least `NOTABLE_MIN_OTHER_STARS` distinct *other* users
star it. This is independent of classification -- it can be set on any
entry via the star reaction or the webapp's notable toggle, regardless of
kind.

## Detector test battery

`/test` (or the webapp's Scripts tab / `scripts/test_detector.py`) posts 6
synthetic photos into `#snipes` and runs each straight through the real
classifier, exercising every branch: mention+snipe, mention+snope, bare
mention, mention with no photo, a do-not-snipe violation (briefly DNS-
listing the bot itself to trigger the real warning DM), and no mention at
all. The bot is always the *sniped* party and Zeke's real user ID is
always the *sniper* -- a test run can never touch a real human's
leaderboard ranking. `/cleartest` (or `scripts/cleartest_detector.py`)
removes every message, warning, and log entry the last run created.

## The webapp

- **Live Log** -- read-only view of the `.xlsx` true log.
- **Review Queue** -- resolve pending photos; supports paging, one-at-a- time mode, and lazy-loaded context/thread replies.
- **Manual Decisions** -- every entry Zeke has ever manually resolved, for browsing or revising.
- **Search** -- filters across *every* entry (any kind, pending or resolved) by sniper, victim, time-of-day bucket, day of week, starred/ manual status, and caption text. Note: this tab's time-of-day buckets (morning/afternoon/evening/**night**) use slightly different labels than the Analysis tab's (morning/afternoon/evening/**late-night**) -- two independent bucketing implementations, not a shared one.
- **Do Not Snipe** -- view/add/remove the consent list.
- **Analysis** -- formatted view of `analysis/stats.py`: leaderboard, rivalries, streaks, "untouchable" award, retaliation speed, peak time windows, notable ratio, group-snipe frequency.
- **Matrix** -- the sniper x victim count matrix, as three switchable views (Coded / Manual / True); see Scoring below.
- **Adjustments** -- manual per-person score adjustments.
- **Scripts** -- runs any maintenance script as a subprocess and streams its console output live.

Both the Matrix and Adjustments tabs are directly editable in place (click
a cell, type, click away to save) and share a zoom control, a sticky
header row, and a sticky first column, so a full roster stays navigable
without losing track of who's who.

## Scoring

Scoring is built in layers, all in `analysis/stats.py` and the **Matrix**/
**Adjustments** webapp tabs:

1. **Coded matrix** -- the sniper x victim count matrix purely as the bot classified it from `snipes.json` (`analysis.snipe_matrix()`). Read-only; only `recount`/`backfill` ever change it.
2. **Manual matrix** -- Zeke's own cell-by-cell corrections/additions on top of that (`manual_matrix.json`, edited from the Matrix tab's **Manual** view). A cell can be positive (a snipe the bot never caught) or negative (correcting one it over-counted). Stored entirely separately from `snipes.json`, so a `recount`/`backfill` pass can never overwrite it.
3. **True matrix** -- Coded + Manual, added cell-by-cell (`analysis.true_matrix()`). This is what Snipes/Sniped actually count for scoring. It's also editable from its own Matrix tab view: editing a True cell just computes the difference from the Coded value and saves *that* to the Manual matrix, so Coded stays untouched no matter which view you edit from.
4. **Adjustments** -- a further, independent per-person nudge (`adjustments.json`, edited from the **Adjustments** tab), separate from the matrices entirely. Can be fractional, unlike matrix cells (which are whole snipe counts).

Final score, per user:

```
Snipes  = that user's True-matrix row total  (how many times they sniped anyone)
Sniped  = that user's True-matrix column total  (how many times they were sniped)
Score   = Snipes - Sniped + Adjustment
```

`Rank` is competition ("1224") style: tied scores share a rank, and
whoever's next skips ahead by however many people were tied (e.g. two
people tied for 3rd are both rank 3, and the next person is rank 5).

## Maintenance scripts

All maintenance scripts live in `scripts/` and are run as modules:

```bash
python -m scripts.recount
python -m scripts.backfill --since 2026-08-15
python -m scripts.resync
python -m scripts.migrate_manuals
python -m scripts.fix_malformed_snipes
python -m scripts.test_detector
python -m scripts.cleartest_detector
python -m scripts.diagnose
```

They're also all runnable from the webapp's **Scripts** tab, which streams
their console output live.

`backfill` **vs** `recount` **are different jobs:**

- `recount` -- run this after changing detection rules. Walks every photo already in `snipes.json`, re-runs the current detection rules on automatic entries, leaves manual decisions alone, migrates legacy ignored/fingerprint/thumbsup NAS entries to black, repairs the bot's classification reaction on every photo, and updates the true log when a classification changes. This is the command that fixes "I changed the keywords but the existing Slack messages didn't change." It also scans Slack from `SEASON_START_DATE` forward first, so photos missing from `snipes.json` get discovered before the recount runs.
- `backfill --since YYYY-MM-DD` -- scans Slack for historical photos not yet in `snipes.json`, adds them, then runs a full recount. Safe to run repeatedly -- a photo is identified by `channel + message_ts`, so it never creates duplicate entries. `bot/catchup.py` runs this same core automatically every `CATCHUP_INTERVAL_SECONDS` (default 6h) using a persisted watermark, so you shouldn't normally need to run it by hand.
- `migrate_manuals` -- one-time recovery for photos classified before `manually_resolved` existed. Checks Slack's reaction user lists and protects states where Zeke himself left a classification reaction; also converts legacy fingerprint/thumbsup NAS and Zeke red corrections. Run once, before your first full recount, if preserving that history matters.
- `resync` -- rebuilds the `.xlsx` true log from `snipes.json` without changing any classification. Use this if the workbook itself needs repairing but the JSON log is correct.
- `fix_malformed_snipes` -- one-time repair for "malformed" entries: `kind == "snipe"` with an empty `sniped` list (possible before the webapp started blocking that combination at entry). Demotes each one back to `unidentified` so it re-enters the Review Queue / Manual Decisions tab with the caption's @mentions pre-suggested. Doesn't guess a sniped user on its own -- there's no reliable way to.
- `test_detector` **/** `cleartest_detector` -- standalone runners for the `/test` and `/cleartest` battery described above, usable from the Scripts tab without going through Slack slash commands.
- `diagnose` -- prints `auth.test`/scope/channel-membership results against the live token; useful for tracking down a silent `chat_postEphemeral` failure or a `not_in_channel` error.

**Rule of thumb: change detection rules → run** `recount`**.** Don't rely on
backfill's delta-only scan as a substitute for recounting the existing log.

## Running in production

```bash
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now snipesbot webapp
sudo systemctl status snipesbot webapp
journalctl -u snipesbot -f
```

## Known quirks worth knowing about

- `web/routes.py` still defines `POST /api/review-queue/<id>/resolve`, but the current frontend (`app.js`) never calls it -- every webapp action goes through the more general `POST /api/entries/<id>/set-kind` instead. Harmless leftover, not wired to anything.
- The context-capture timers described above are in-memory only and don't survive a bot restart mid-window (see Review queue section).
- Two independent time-of-day bucketing implementations exist (Search tab vs. Analysis tab) with slightly different label strings for the same concept -- see the webapp section above.
- Matrix cells (Coded/Manual/True) only accept whole numbers -- they're snipe *counts*. Only Adjustments accepts fractional values.