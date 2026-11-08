"""
Command line entry point:  python -m snipesbot <command>

    migrate [--from DIR] [--recover-manuals]   import the old JSON files once
    sync [--since YYYY-MM-DD] [--full] [--repair-reactions]
    export [PATH]                              write the .xlsx log
    stats                                      print the leaderboard
    diagnose                                   check Slack access and data
    selftest | selftest-clear                  post / remove synthetic test photos
    bot                                        run the Slack bot (same as app.py)
    web                                        run the admin UI (same as webapp.py)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .env import Env
from .services import exports, legacy, maintenance
from .services import sync as sync_service
from .slack_io import make_client
from .store import Busy


def _env() -> Env:
    cfg = load_config()
    return Env.create(cfg, slack=make_client(cfg.bot_token))


def cmd_migrate(args) -> int:
    env = _env()
    src = Path(args.src) if args.src else env.cfg.data_dir
    if not (src / "snipes.json").exists():
        print(f"No snipes.json in {src}; nothing to import.", file=sys.stderr)
        return 1
    legacy.import_legacy(env, src)
    if args.recover_manuals:
        legacy.recover_manual_decisions(env)
    print("Done. Your old files were not modified; keep them as a backup.")
    return 0


def cmd_sync(args) -> int:
    env = _env()
    since = maintenance._date_to_ts(env, args.since) if args.since else None
    try:
        sync_service.run_sync(env, since=since, full=args.full, repair_reactions=args.repair_reactions)
    except Busy as exc:
        print(f"Not started: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_export(args) -> int:
    env = _env()
    n = exports.write_xlsx(env, args.path)
    print(f"Wrote {n} entries to {args.path}")
    return 0


def cmd_stats(args) -> int:
    from .analysis.stats import Stats
    from .services import people
    env = _env()
    names = env.store.names.all()
    print(f"{'#':>3}  {'Name':<32} {'Snipes':>6} {'Sniped':>6} {'Adj':>5} {'Score':>6}")
    for r in Stats(env).leaderboard():
        print(f"{r['rank']:>3}  {people.display(r['user'], names):<32} {r['snipes']:>6} {r['sniped']:>6} "
              f"{r['adjustment']:>5g} {r['score']:>6g}")
    return 0


def cmd_task(key):
    def run(args) -> int:
        maintenance.TASKS[key].run(_env(), {}, print)
        return 0
    return run


def cmd_bot(args) -> int:
    from .runtime import run_bot
    run_bot()
    return 0


def cmd_web(args) -> int:
    from .runtime import run_web
    run_web()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="snipesbot", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    m = sub.add_parser("migrate", help="import the old JSON files into the database")
    m.add_argument("--from", dest="src", help="directory holding snipes.json etc (default: the data dir)")
    m.add_argument("--recover-manuals", action="store_true", help="also infer manual decisions from Zeke's reactions")
    m.set_defaults(func=cmd_migrate)

    s = sub.add_parser("sync", help="archive, discover, recount")
    s.add_argument("--since", help="rescan from this date (YYYY-MM-DD)")
    s.add_argument("--full", action="store_true", help="rescan from the season start")
    s.add_argument("--repair-reactions", action="store_true", help="verify every reaction on Slack")
    s.set_defaults(func=cmd_sync)

    e = sub.add_parser("export", help="write the .xlsx log")
    e.add_argument("path", nargs="?", default="snipes_log.xlsx")
    e.set_defaults(func=cmd_export)

    sub.add_parser("stats", help="print the leaderboard").set_defaults(func=cmd_stats)
    sub.add_parser("diagnose", help="check Slack access and data").set_defaults(func=cmd_task("diagnose"))
    sub.add_parser("selftest", help="post synthetic test photos").set_defaults(func=cmd_task("selftest"))
    sub.add_parser("selftest-clear", help="remove them").set_defaults(func=cmd_task("selftest_clear"))
    sub.add_parser("bot", help="run the Slack bot").set_defaults(func=cmd_bot)
    sub.add_parser("web", help="run the admin UI").set_defaults(func=cmd_web)
    return p


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
