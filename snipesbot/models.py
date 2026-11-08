"""
The domain model. One `Entry` per photo (plus derived entries split out of a
"special" photo), with one `Kind` enum instead of string literals scattered
through the code.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, tzinfo
from enum import Enum
from typing import Optional


class Kind(str, Enum):
    UNIDENTIFIED = "unidentified"        # no usable evidence; needs a human
    LIKELY_SNIPE = "likely_snipe"        # bare @mention; probably a snipe
    SNIPE = "snipe"                      # confirmed snipe (counts toward score)
    SNOPE = "snope"                      # joke / fake snipe
    NOT_A_SNIPE = "not_a_snipe"          # human-confirmed NAS
    INCORRECT_SNIPE = "incorrect_snipe"  # a confirmed snipe that was wrong
    SPECIAL = "special"                  # too niche for the detector

    @property
    def label(self) -> str:
        return KIND_LABELS[self]


KIND_LABELS = {
    Kind.UNIDENTIFIED: "Unidentified",
    Kind.LIKELY_SNIPE: "Likely snipe",
    Kind.SNIPE: "Snipe",
    Kind.SNOPE: "Snope",
    Kind.NOT_A_SNIPE: "NAS",
    Kind.INCORRECT_SNIPE: "Missnipe",
    Kind.SPECIAL: "Special",
}

#: Kinds that still need a human decision.
PENDING_KINDS = frozenset({Kind.UNIDENTIFIED, Kind.LIKELY_SNIPE})

#: Kinds a person may pick when resolving a pending photo from Slack.
RESOLVABLE_KINDS = frozenset({Kind.SNIPE, Kind.SNOPE, Kind.NOT_A_SNIPE})

_SLACK_ID_RE = re.compile(r"^[UW][A-Z0-9]{2,}$")


def is_slack_user_id(value: Optional[str]) -> bool:
    """True for a real Slack account id. Anything else (e.g. "deer", typed
    on the Special tab) is a free-text name: displayed as-is and exempt from
    anything that assumes a real account (DMs, lookups, do-not-snipe)."""
    return bool(value) and bool(_SLACK_ID_RE.match(value))


def new_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class FileRef:
    """The slim reference to an attached image/video. Bytes live in the
    archive, keyed by `id`; nothing else about the Slack file is kept (its
    URLs go stale)."""
    id: str
    name: str = ""
    mimetype: str = ""

    @property
    def is_video(self) -> bool:
        return self.mimetype.startswith("video/")

    @classmethod
    def from_slack(cls, f: dict) -> "FileRef":
        return cls(id=f.get("id") or "", name=f.get("name") or "", mimetype=f.get("mimetype") or "")

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "mimetype": self.mimetype}

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> Optional["FileRef"]:
        if not d or not d.get("id"):
            return None
        return cls(id=d["id"], name=d.get("name", ""), mimetype=d.get("mimetype", ""))


@dataclass(frozen=True)
class Entry:
    id: str
    kind: Kind
    sniper: str
    channel: str
    message_ts: str
    sniped: tuple = ()
    caption: str = ""
    file: Optional[FileRef] = None
    extra_files: tuple = ()
    star_users: tuple = ()
    notable_auto: bool = False                 # derived from star reactions
    notable_override: Optional[bool] = None    # set by hand from the webapp
    manually_resolved: bool = False
    derived_from: Optional[str] = None         # parent "special" entry id
    reacted_kind: Optional[Kind] = None        # kind the bot's reaction last matched on Slack
    source: str = "live"                       # "live" | "historical"
    created_at: float = 0.0
    notified_at: Optional[float] = None
    deleted_at: Optional[float] = None

    # --- derived ---------------------------------------------------------
    @property
    def posted_at(self) -> float:
        return float(self.message_ts)

    @property
    def group_snipe(self) -> bool:
        return len(self.sniped) > 1

    @property
    def notable(self) -> bool:
        return self.notable_auto if self.notable_override is None else self.notable_override

    @property
    def pending(self) -> bool:
        return self.kind in PENDING_KINDS

    @property
    def deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_derived(self) -> bool:
        return self.derived_from is not None

    def local_time(self, tz: tzinfo) -> datetime:
        return datetime.fromtimestamp(self.posted_at, tz)

    def with_(self, **changes) -> "Entry":
        return replace(self, **changes)
