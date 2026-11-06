"""
Backing logic for the webapp's Scripts tab -- runs the scripts in scripts/
(and analysis/stats.py) as subprocesses and streams their output back over
HTTP polling. Add a future one-off script here with one entry each.

SECURITY NOTE: there is no authentication on any of this -- see the module
docstring in web/app.py.
"""
import subprocess
import sys
import threading
import uuid

PROJECT_DIR = None  # set by web/app.py at startup, so subprocesses run with
                     # the project root as cwd regardless of who imports this

# "module" is passed to `python -m`, so each of these must be an importable
# module with a __main__ guard. "needs_since" adds a date field to that
# script's card in the UI, passed through as --since.
SCRIPTS = {
    "backfill": {
        "label": "Backfill", "module": "scripts.backfill", "needs_since": True,
        "description": "Manual historical scan from a given date -- same core as the automatic catch-up. "
                        "Also syncs the local archive before discovering/classifying anything.",
    },
    "archive_sync": {
        "label": "Sync Local Archive", "module": "scripts.archive_sync", "needs_since": False,
        "description": "Downloads every message and file from #snipes into the permanent local archive right "
                        "now, without touching snipes.json -- run this before anything approaches Slack's "
                        "90-day retention limit.",
    },
    "migrate_manuals": {
        "label": "Migrate Historical Manual Decisions", "module": "scripts.migrate_manuals", "needs_since": False,
        "description": "One-time best-effort recovery of old Zeke decisions by checking who owns the Slack state reaction.",
    },
    "recount": {
        "label": "Recount / Repair Reactions", "module": "scripts.recount", "needs_since": False,
        "description": "Re-runs current detection rules over every automatic entry and normalizes every Slack state reaction. Manual decisions are protected.",
    },
    "resync": {
        "label": "Resync", "module": "scripts.resync", "needs_since": False,
        "description": "Rebuilds the true log from snipes.json without changing classifications.",
    },
    "fix_malformed_snipes": {
        "label": "Fix Malformed Snipes", "module": "scripts.fix_malformed_snipes", "needs_since": False,
        "description": "Demotes any 'snipe' entry with an empty sniped list back to unidentified, so it re-enters "
                        "the Review Queue / Manual Decisions tab with the caption's @mentions pre-suggested for you to confirm.",
    },
    "test_detector": {
        "label": "Run Detector Test Battery", "module": "scripts.test_detector", "needs_since": False,
        "description": "Posts 6 synthetic test photos into #snipes exercising every detection branch, "
                        "including a real do-not-snipe warning DM.",
    },
    "cleartest_detector": {
        "label": "Clear Detector Test Messages", "module": "scripts.cleartest_detector", "needs_since": False,
        "description": "Removes every message, do-not-snipe warning, and log entry created by the last test-battery run.",
    },
    "analysis": {
        "label": "Analysis (raw)", "module": "analysis.stats", "needs_since": False,
        "description": "Prints the full stats report to the console (see the Analysis tab for a formatted view).",
    },
    "diagnose": {
        "label": "Diagnose", "module": "scripts.diagnose", "needs_since": False,
        "description": "Connectivity/scope sanity check against the live Slack token.",
    },
}

_runs = {}  # run_id -> {"output": [...], "done": bool, "returncode": int|None}
_runs_lock = threading.Lock()


def _stream_output(run_id, process):
    for line in iter(process.stdout.readline, ""):
        with _runs_lock:
            _runs[run_id]["output"].append(line)
    process.wait()
    with _runs_lock:
        _runs[run_id]["done"] = True
        _runs[run_id]["returncode"] = process.returncode


def start_run(name: str, since: str = None) -> str:
    """Launches script `name` as a subprocess and returns a run_id to poll
    with get_run(). Raises KeyError for an unknown script name and
    ValueError if `since` is required but missing."""
    spec = SCRIPTS[name]
    args = [sys.executable, "-m", spec["module"]]
    if spec["needs_since"]:
        if not since:
            raise ValueError("since date required (YYYY-MM-DD)")
        args += ["--since", since]

    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, cwd=PROJECT_DIR,
    )
    run_id = str(uuid.uuid4())
    with _runs_lock:
        _runs[run_id] = {"output": [], "done": False, "returncode": None}
    threading.Thread(target=_stream_output, args=(run_id, process), daemon=True).start()
    return run_id


def get_run(run_id: str):
    with _runs_lock:
        run = _runs.get(run_id)
        if not run:
            return None
        return {"output": "".join(run["output"]), "done": run["done"], "returncode": run["returncode"]}
