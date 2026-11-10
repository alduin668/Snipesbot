"""
One-time migration from the old JSON/xlsx layout into the database.

    python -m snipesbot migrate [--from DIR] [--recover-manuals]

Reads (all optional): snipes.json, do_not_snipe.json, dns_warnings.json,
settings.json, adjustments.json, manual_matrix.json, user_names.json,
test_messages.json, archive/messages.json, archive/files_index.json, and
photos/. The old files are left untouched as a backup. Re-running is safe:
entries are matched by id, so nothing is duplicated.

Behaviour changes made while importing:
  * legacy "ignored" entries become manual NAS (what recount used to do)
  * snipes with an empty sniped list (the old "malformed snipes": a photo marked
    green by reaction, with no #snipe reply to say who) stay snipes, but show up in
    the Review Queue as "who was sniped?" until someone fills it in
  * `status`, `ignored`, `context_*`, `photo_path` are dropped: context now
    comes from the archive, and photos are served from the archive
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..env import Env
from ..models import Entry, FileRef, Kind
from . import entries as entry_service

log = logging.getLogger("snipesbot")
Progress = Callable[[str], None]


@dataclass
class ImportReport:
    entries: int = 0
    skipped: int = 0
    messages: int = 0
    files: int = 0
    incomplete_snipes: int = 0
    dns_users: int = 0
    converted_ignored: int = 0
    missing_files: list = field(default_factory=list)    # paths the old index named but that weren't on disk
    missing_photos: int = 0                              # entries whose photo couldn't be located at all
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        text = (f"{self.entries} entries imported ({self.skipped} already present), "
                f"{self.messages} archived messages, {self.files} files, "
                f"{self.converted_ignored} legacy-ignored -> NAS, {self.incomplete_snipes} snipes need a sniped user, "
                f"{self.dns_users} do-not-snipe")
        if self.missing_files or self.missing_photos:
            text += f"; {len(self.missing_files)} archived file(s) and {self.missing_photos} photo(s) NOT FOUND"
        return text


def _load(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r") as f:
        return json.load(f)


def _file_ref(raw: Optional[dict]) -> Optional[FileRef]:
    if not raw or not raw.get("id"):
        return None
    mimetype = raw.get("mimetype") or ("video/mp4" if raw.get("is_video") else "image/jpeg")
    return FileRef(id=raw["id"], name=raw.get("name") or "", mimetype=mimetype)


def _find_file(legacy_dir: Path, recorded: str, *subdirs: str) -> Optional[Path]:
    """Legacy paths were relative to wherever the bot happened to run; look in
    the places they could have been."""
    if not recorded:
        return None
    name = Path(recorded).name
    for candidate in (Path(recorded), legacy_dir / recorded, *[legacy_dir.joinpath(*subdirs, name)]):
        if candidate.exists():
            return candidate
    return None


def import_legacy(env: Env, legacy_dir: Path, progress: Progress = print) -> ImportReport:
    legacy_dir = Path(legacy_dir)
    report = ImportReport()
    store = env.store

    _import_archive(env, legacy_dir, report, progress)

    for raw in _load(legacy_dir / "snipes.json", []):
        if store.entries.get(raw["id"]) is not None:
            report.skipped += 1
            continue
        store.entries.insert(_convert_entry(env, legacy_dir, raw, report))
        report.entries += 1

    dns_file = legacy_dir / "do_not_snipe.json"
    if dns_file.exists():
        users = [u for u in _load(dns_file, []) if isinstance(u, str) and u]
        for user in users:
            store.dns.add(user)
        report.dns_users = len(users)
    else:
        report.notes.append(f"do_not_snipe.json was not found in {legacy_dir}: the do-not-snipe list was NOT imported")
    for w in _load(legacy_dir / "dns_warnings.json", []):
        if store.dns.reserve_warning(w["channel"], w["snipe_ts"], w["target"], w.get("sniper", "")):
            store.dns.record_warning_message(w["channel"], w["snipe_ts"], w["target"],
                                             w.get("warning_channel", ""), w.get("warning_ts", ""))
    settings = _load(legacy_dir / "settings.json", {})
    if "dm_review_enabled" in settings:
        store.settings.set("dm_review_enabled", bool(settings["dm_review_enabled"]))
    if settings.get("last_catchup_ts"):
        store.settings.set("last_sync_ts", settings["last_catchup_ts"])
    for user, value in _load(legacy_dir / "adjustments.json", {}).items():
        store.scoring.set_adjustment(user, float(value))
    for sniper, row in _load(legacy_dir / "manual_matrix.json", {}).items():
        for victim, value in row.items():
            store.scoring.set_cell(sniper, victim, int(value))
    for user, name in _load(legacy_dir / "user_names.json", {}).items():
        store.names.set(user, name)
    for t in _load(legacy_dir / "test_messages.json", []):
        store.tests.add(t["channel"], t["ts"], t.get("label", ""))

    progress(f"[migrate] {report.summary()}")
    for note in report.notes:
        progress(f"[migrate] WARNING: {note}")
    if report.missing_files or report.missing_photos:
        progress(_missing_hint(legacy_dir, report))
    return report


def _missing_hint(legacy_dir: Path, report: ImportReport) -> str:
    lines = ["[migrate] WARNING: some photo files were not found, so those photos will show as missing.",
             f"[migrate]   looked in: {legacy_dir / 'archive' / 'files'}  and  {legacy_dir / 'photos'}"]
    for path in report.missing_files[:3]:
        lines.append(f"[migrate]   e.g. the old index expected: {path}")
    lines.append("[migrate]   If the files exist elsewhere, reconnect them with:  "
                 "python -m snipesbot relink-files /path/to/the/old/data")
    return "\n".join(lines)


def _convert_entry(env: Env, legacy_dir: Path, raw: dict, report: ImportReport) -> Entry:
    kind = Kind(raw.get("kind", "unidentified"))
    sniped = tuple(raw.get("sniped") or ())
    manual = bool(raw.get("manually_resolved"))

    if raw.get("ignored"):                       # legacy fingerprint/"ignored" == NAS
        kind, sniped, manual = Kind.NOT_A_SNIPE, (), True
        report.converted_ignored += 1
    elif kind in (Kind.NOT_A_SNIPE, Kind.INCORRECT_SNIPE):
        manual = True                            # these were always human decisions
    if kind is Kind.SNIPE and not sniped:        # the old "malformed snipe": keep it, flag it
        report.incomplete_snipes += 1

    file = _file_ref(raw.get("file"))
    extras = tuple(f for f in (_file_ref(x) for x in raw.get("extra_files") or []) if f)
    _adopt_photo(env, legacy_dir, raw, file, report)

    stars = tuple(raw.get("star_users") or ())
    zeke = env.cfg.zeke_user_id
    auto_notable = zeke in stars or len(set(stars) - {zeke}) >= env.cfg.notable_min_other_stars
    legacy_notable = bool(raw.get("notable"))
    ts = raw["message_ts"]
    derived = raw.get("derived_from")
    return Entry(
        id=raw["id"], kind=kind, sniper=raw["sniper"], sniped=sniped, channel=raw["channel"],
        message_ts=ts, caption=raw.get("photo_text") or "", file=file, extra_files=extras,
        star_users=stars, notable_auto=auto_notable,
        notable_override=legacy_notable if legacy_notable != auto_notable else None,
        manually_resolved=manual, derived_from=derived,
        reacted_kind=None if derived else kind,      # assume Slack already matched
        source="historical", created_at=float(ts),
    )


def _adopt_photo(env: Env, legacy_dir: Path, raw: dict, file: Optional[FileRef], report: ImportReport) -> None:
    """Old entries kept their own copy of the photo under photos/. If the archive
    doesn't already hold that file, adopt the copy so nothing is lost."""
    if not file or env.store.archive.has_file(file.id):
        return
    src = _find_file(legacy_dir, raw.get("photo_path") or "", "photos")
    if src:
        env.store.archive.adopt_file(file.id, src, file.name or src.name, file.mimetype)
        report.files += 1
    else:
        report.missing_photos += 1


def _import_archive(env: Env, legacy_dir: Path, report: ImportReport, progress: Progress) -> None:
    messages = _load(legacy_dir / "archive" / "messages.json", {})
    batch = [dict(m, channel=m.get("channel") or env.cfg.channel_id, ts=m.get("ts") or ts)
             for ts, m in messages.items()]
    report.messages = env.store.archive.save_messages(batch)

    index = _load(legacy_dir / "archive" / "files_index.json", {})
    for file_id, info in index.items():
        if env.store.archive.has_file(file_id):
            continue
        src = _find_file(legacy_dir, info.get("path", ""), "archive", "files")
        if not src:
            report.missing_files.append(info.get("path", "") or file_id)
            continue
        in_place = src.resolve().parent == env.cfg.archive_files_dir.resolve()
        if in_place:
            env.store.archive.register_existing(file_id, src.name, info.get("name", ""), info.get("mimetype", ""))
        else:
            env.store.archive.adopt_file(file_id, src, info.get("name", ""), info.get("mimetype", ""))
        report.files += 1
    progress(f"[migrate] archive: {report.messages} messages, {report.files} files")


# ---------------------------------------------------------------------------
# one-time recovery of decisions made before `manually_resolved` existed
# ---------------------------------------------------------------------------

def recover_manual_decisions(env: Env, progress: Progress = print) -> int:
    """Before `manually_resolved` existed, the log couldn't say whether a green or
    purple state was picked by the bot or by Zeke. The best evidence left is who
    placed the reaction on Slack: if Zeke himself reacted with a classification
    emoji, treat that state as his manual decision. Run once, before the first
    full sync, if historical decisions need protecting. Costs one Slack call per
    undecided entry."""
    e = env.cfg.emojis
    zeke = env.cfg.zeke_user_id
    changed = checked = 0
    for entry in env.store.entries.all_active():
        if entry.manually_resolved or entry.is_derived:
            continue
        checked += 1
        try:
            resp = env.slack.reactions_get(channel=entry.channel, timestamp=entry.message_ts)
        except Exception as exc:  # noqa: BLE001
            progress(f"[recover] reactions.get failed for {entry.message_ts}: {exc}")
            continue
        mine = {r.get("name") for r in (resp.get("message") or {}).get("reactions", []) or []
                if zeke in r.get("users", [])}

        target = None
        if e.incorrect_snipe in mine and entry.kind is Kind.SNIPE:
            target = (Kind.INCORRECT_SNIPE, ())
        elif "thumbsup" in mine:
            target = (Kind.NOT_A_SNIPE, ())
        elif e.special in mine:
            target = (Kind.SPECIAL, ())
        elif e.snipe in mine:
            target = (Kind.SNIPE, None)
        elif e.snope in mine:
            target = (Kind.SNOPE, ())
        elif e.likely_snipe in mine:
            target = (Kind.LIKELY_SNIPE, None)
        if target is None:
            continue
        kind, sniped = target
        try:
            entry_service.apply_state(env, entry.id, kind, sniped=sniped)
        except entry_service.InvalidTransition:
            continue  # e.g. a green reaction on a photo with nobody to snipe
        progress(f"[recover] {entry.id}: Zeke's reaction -> manual {kind.value}")
        changed += 1
    progress(f"[recover] checked {checked}, protected {changed} historical manual decision(s)")
    return changed
