"""Load `.env` into the process environment (never prints or logs values).

Skipped under pytest so tests can never pick up a real key by accident; set
TREASURY_NO_DOTENV=1 to skip elsewhere. Real environment variables win over `.env`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_loaded = False
STATE_FILE = Path(__file__).resolve().parents[2] / ".treasury-state.json"
_STATE_ENV = {"connect_account_a": "STRIPE_CONNECT_ACCOUNT_A",
              "connect_account_b": "STRIPE_CONNECT_ACCOUNT_B",
              "issuing_cardholder": "STRIPE_ISSUING_CARDHOLDER",
              "issuing_card": "STRIPE_ISSUING_CARD"}


def read_state(path: str | Path | None = None) -> dict:
    """Non-secret ids written by scripts/bootstrap_sandbox.py (gitignored)."""
    import json
    try:
        return json.loads(Path(path or STATE_FILE).read_text())
    except (OSError, ValueError):
        return {}


def write_state(state: dict, path: str | Path | None = None) -> None:
    import json
    Path(path or STATE_FILE).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def load_state_env(path: str | Path | None = None) -> None:
    for k, env in _STATE_ENV.items():
        v = read_state(path).get(k)
        if v and not os.environ.get(env):
            os.environ[env] = str(v)


def load_env(path: str | Path | None = None, force: bool = False) -> bool:
    """Returns True if a .env file was loaded."""
    global _loaded
    if not force and (_loaded or "pytest" in sys.modules or os.environ.get("TREASURY_NO_DOTENV")):
        return False
    _loaded = True
    from dotenv import load_dotenv
    candidates = [Path(path)] if path else [Path.cwd() / ".env",
                                            Path(__file__).resolve().parents[2] / ".env"]
    loaded = False
    for p in candidates:
        if p.is_file():
            loaded = bool(load_dotenv(p, override=False))
            break
    load_state_env()  # after .env so explicit settings win over bootstrap state
    return loaded
