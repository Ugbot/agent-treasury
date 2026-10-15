"""Check Cognee configuration and (optionally) round-trip one test memory. Prints no secrets.

Usage: uv run python scripts/cognee_setup.py [--write]
"""
from __future__ import annotations

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv optional
    pass

from treasury.memory import CogneeMemory, cognee_mode


def main() -> int:
    mode = cognee_mode()
    print(f"mode: {mode}")
    print(f"COGNEE_API_KEY set: {bool(os.environ.get('COGNEE_API_KEY'))}")
    print(f"COGNEE_SERVICE_URL set: {bool(os.environ.get('COGNEE_SERVICE_URL'))}")
    print(f"LLM_API_KEY set: {bool(os.environ.get('LLM_API_KEY'))}")
    mem = CogneeMemory()
    st = mem.status(probe=True)
    print(f"status: {st}")
    if mode == "none":
        print("Set COGNEE_SERVICE_URL + COGNEE_API_KEY (Cognee Cloud) or LLM_API_KEY (local) in .env.")
        return 1
    if "--write" in sys.argv:
        w = mem.write_policy("setup-check", 1, "0" * 8, "Clause SETUP-1: setup check policy.")
        print(f"write: {w.status} {w.message}")
        return 0 if w.ok else 2
    return 0 if st["reachable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
