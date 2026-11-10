"""Access to the shared Env from request handlers."""
from flask import current_app

from ..env import Env


def env() -> Env:
    return current_app.config["ENV"]
