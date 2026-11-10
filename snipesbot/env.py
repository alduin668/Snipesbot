"""
`Env` is what every service receives: configuration, the store, and a Slack
client. Passing it explicitly (instead of module-level globals) is what makes
the services testable with a fake Slack client and a temp database.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import Config
from .store import Store

log = logging.getLogger("snipesbot")


@dataclass
class Env:
    cfg: Config
    store: Store
    slack: Any = None                      # slack_sdk.WebClient (or a test double)
    _bot_user_id: Optional[str] = field(default=None, repr=False)
    _name_misses: dict = field(default_factory=dict, repr=False)   # user id -> when a lookup last failed

    @classmethod
    def create(cls, cfg: Config, slack: Any = None) -> "Env":
        return cls(cfg=cfg, store=Store.open(cfg), slack=slack)

    def bot_user_id(self) -> Optional[str]:
        if self._bot_user_id is None and self.slack is not None:
            try:
                self._bot_user_id = self.slack.auth_test().get("user_id")
            except Exception as exc:  # noqa: BLE001 - network/Slack errors are expected
                log.warning("auth.test failed: %s", exc)
        return self._bot_user_id

    def workspace_url(self) -> Optional[str]:
        url = self.store.settings.get("workspace_url")
        if not url and self.slack is not None:
            try:
                url = self.slack.auth_test().get("url")
                if url:
                    self.store.settings.set("workspace_url", url)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not learn workspace url: %s", exc)
        return url

    def permalink(self, channel: str, ts: str) -> str:
        """Build a message permalink locally; no Slack call per row."""
        base = self.workspace_url()
        if not base:
            return ""
        return f"{base.rstrip('/')}/archives/{channel}/p{ts.replace('.', '')}"
