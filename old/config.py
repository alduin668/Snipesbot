"""
SnipesBot configuration.

Everything here is either a secret loaded from the environment (see
.env.example) or a tunable constant. Nothing in this file talks to Slack
or touches disk -- it's safe to import from anywhere (bot code, scripts,
the webapp) without side effects.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Slack ---
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]          # xoxb-...
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]          # xapp-...
SNIPES_CHANNEL_ID = os.environ["SNIPES_CHANNEL_ID"]      # channel ID of #snipes (not the name)
ZEKE_USER_ID = os.environ["ZEKE_USER_ID"]                # Zeke's Slack user ID

# Who gets DM'd unidentified photos when /reviewmode is on. Defaults to Zeke.
# (Using `or` rather than get()'s default arg: an empty REVIEWER_USER_ID= line
# in .env still counts as "set" to os.environ, so get()'s default wouldn't
# kick in -- `or` correctly falls back on any falsy/blank value too.)
REVIEWER_USER_ID = os.environ.get("REVIEWER_USER_ID") or ZEKE_USER_ID

# --- Live log webapp (web/) ---
WEBAPP_HOST = os.environ.get("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT", "8420"))

# --- Detection rules ---
# Matched case-insensitively as substrings of the photo's caption.
SNOPE_KEYWORDS = {"snope", "snoped", "snopes", "snoping"}
SNIPE_KEYWORDS = {"snipe", "sniped", "snipes", "sniping"}
STAR_EMOJI = "star"           # reaction name (no colons) marking a snipe "notable"
NOTABLE_MIN_OTHER_STARS = 3   # notable if Zeke stars it OR >= this many *other* distinct users star it

# --- Reactions for the classification states ---
# These are the only classification reactions SnipesBot owns.
SNIPE_EMOJI = "large_green_circle"          # confirmed snipe
SNOPE_EMOJI = "large_purple_circle"         # joke / fake snipe
UNIDENTIFIED_EMOJI = "question"             # needs human identification
LIKELY_SNIPE_EMOJI = "large_orange_circle"  # likely snipe, not confirmed
NOT_A_SNIPE_EMOJI = "black_circle"          # NAS; human-resolved, excluded from scoring
INCORRECT_SNIPE_EMOJI = "red_circle"        # Zeke-only correction of a confirmed snipe
SPECIAL_EMOJI = "fingerprint"                # too-niche-for-code manual entries (see the Special tab)

# No longer applied automatically -- analysis/stats.py's leaderboard() now
# scores purely as Snipes - Sniped + a manual Adjustment (see
# ADJUSTMENTS_JSON above). Kept here only in case something still wants to
# reference "what an incorrect snipe used to cost" for display purposes.
INCORRECT_SNIPE_PENALTY = -1

# --- Review queue (bot/review.py) ---
# How many messages before/after an unidentified photo to log as a hint for
# whoever reviews it. Purely informational -- never affects classification.
CONTEXT_WINDOW = 2
# How long to keep collecting "after" context before finalizing/notifying,
# unless CONTEXT_WINDOW messages arrive sooner (in which case it's immediate).
CONTEXT_CAPTURE_DELAY_SECONDS = int(os.environ.get("CONTEXT_CAPTURE_DELAY_SECONDS", "90"))

# --- Local storage paths ---
DATA_DIR = os.environ.get("SNIPESBOT_DATA_DIR", "data")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")

# --- Permanent local archive (storage/archive_store.py, bot/archiver.py) ---
# Slack's free tier only keeps messages and files for this many days before
# they become permanently inaccessible via the API. Everything the archiver
# does exists to make sure nothing SnipesBot cares about is still depending
# on Slack still having it once that window closes.
SLACK_MESSAGE_RETENTION_DAYS = int(os.environ.get("SLACK_MESSAGE_RETENTION_DAYS", "90"))
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
ARCHIVE_MESSAGES_JSON = os.path.join(ARCHIVE_DIR, "messages.json")
ARCHIVE_FILES_DIR = os.path.join(ARCHIVE_DIR, "files")
ARCHIVE_FILE_INDEX_JSON = os.path.join(ARCHIVE_DIR, "files_index.json")
SNIPES_JSON = os.path.join(DATA_DIR, "snipes.json")
DNS_JSON = os.path.join(DATA_DIR, "do_not_snipe.json")
DNS_WARNINGS_JSON = os.path.join(DATA_DIR, "dns_warnings.json")
SETTINGS_JSON = os.path.join(DATA_DIR, "settings.json")

# Manual, per-person score adjustments (see storage/json_store.py and
# analysis/stats.py's leaderboard()). Fully manual, can be fractional or
# negative -- this is now the ONLY thing that nudges a score by hand;
# there's no more automatic incorrect_snipe penalty baked into scoring.
ADJUSTMENTS_JSON = os.path.join(DATA_DIR, "adjustments.json")

# Manual sniper->victim count corrections/additions, layered on top of the
# bot's own "coded" matrix to produce the "true" matrix scoring is actually
# based on (see analysis/stats.py's true_matrix()). Kept entirely separate
# from snipes.json so that re-running recount/backfill (which only ever
# touches the coded side) can never wipe out a manual entry.
MANUAL_MATRIX_JSON = os.path.join(DATA_DIR, "manual_matrix.json")

# The "true log" (.xlsx, replaces the old Google Sheets integration)
XLSX_PATH = os.environ.get("XLSX_PATH") or os.path.join(DATA_DIR, "snipes_log.xlsx")

# Slack user ID -> display name cache (storage/user_names.py). Only the bot
# process ever calls Slack's users.info; the webapp and analysis just read
# this file, same as they already read the .xlsx/.json without a Slack token.
USER_NAMES_JSON = os.path.join(DATA_DIR, "user_names.json")

# --- Automatic catch-up (bot/catchup.py) ---
# Replaces having to remember to run scripts/backfill.py by hand: the live
# bot re-scans #snipes on a timer and folds in anything it doesn't already
# have. The very first pass ever run (no watermark saved yet) starts from
# here -- every pass after that starts from the end of the previous pass
# instead, so routine catch-up doesn't re-fetch the whole season's history
# every time.
SEASON_START_DATE = os.environ.get("SEASON_START_DATE", "2026-08-15")
CATCHUP_INTERVAL_SECONDS = int(os.environ.get("CATCHUP_INTERVAL_SECONDS", str(6 * 60 * 60)))  # every 6 hours

# How often the background loop checks whether Slack is actually reachable
# (a cheap auth.test call). On regaining connectivity after an outage, an
# immediate catch-up pass runs right away instead of waiting for the next
# CATCHUP_INTERVAL_SECONDS tick -- see bot/catchup.py.
CONNECTIVITY_CHECK_INTERVAL_SECONDS = int(os.environ.get("CONNECTIVITY_CHECK_INTERVAL_SECONDS", "30"))

# --- Detector test battery (/test, /cleartest, scripts/test_detector.py) ---
# No target user id needed -- the "sniped" party in every synthetic test
# case is always the bot itself (see bot/testing.py), so a test run can
# never touch a real human's leaderboard ranking, whether or not /cleartest
# gets run promptly afterward.
TEST_MESSAGES_JSON = os.path.join(DATA_DIR, "test_messages.json")
