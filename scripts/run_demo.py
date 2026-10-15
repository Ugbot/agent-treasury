"""Run all four scenarios through the HTTP API on a fresh database; print Stripe ids and audit verify.

    uv run python scripts/run_demo.py            # real Stripe TEST mode if .env/STRIPE_SECRET_KEY has an sk_test_ key; --mock to force the mock
    # (mock gateway unless STRIPE_SECRET_KEY (sk_test_) is set
    uv run python scripts/run_demo.py --url http://127.0.0.1:8000   # use an already running API instead

Without --url it starts its own uvicorn on a free port with a throwaway database, so the
audit chain verified at the end covers exactly this run. Never prints secrets.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

SCENARIOS = ["deliver_and_release", "fuel_entitlement", "coverage_replan", "cancel_refund"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(tmp: Path, mock: bool = False) -> tuple[subprocess.Popen, str]:
    port = free_port()
    env = {**os.environ, "TREASURY_DB": str(tmp / "demo.sqlite"), "TREASURY_DBOS_DB": str(tmp / "demo_dbos.sqlite")}
    if mock:
        env["TREASURY_NO_DOTENV"] = "1"
        for k in [k for k in env if k.startswith("STRIPE_")]:
            del env[k]
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "treasury.api:app", "--factory",
                          "--port", str(port), "--log-level", "warning"], env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/health", timeout=1).status_code == 200:
                return p, base
        except httpx.HTTPError:
            time.sleep(0.2)
    p.terminate()
    raise SystemExit("API did not start")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--mock", action="store_true", help="force the mock gateway (ignore .env and STRIPE_* vars)")
    args = ap.parse_args()
    proc = None
    if args.url:
        base = args.url.rstrip("/")
    else:
        tmp = Path(tempfile.mkdtemp(prefix="treasury-demo-"))
        proc, base = start_server(tmp, args.mock)
    admin = {"X-Party-Role": "admin", "X-Party-Id": "demo"}
    rc = 0
    try:
        c = httpx.Client(base_url=base, timeout=120)
        print("gateway:", c.get("/health").json())
        for sid in SCENARIOS:
            r = c.post(f"/api/scenarios/{sid}/run", json={"run_id": f"demo-{sid}"})
            body = r.json()
            if r.status_code == 200 and body.get("status") == "awaiting_approval":
                r = c.post(f"/api/scenarios/{sid}/run", json={"run_id": f"demo-{sid}", "approvals": ["merchant"]})
                body = r.json()
            print(f"\n== {sid}: HTTP {r.status_code} status={body.get('status')} mode={body.get('mode')}")
            if r.status_code != 200 or body.get("unavailable"):
                print("   unavailable/failed:", body.get("unavailable") or body.get("detail"))
                rc = 1
            for st in body.get("steps", []):
                label = st.get("name") or st.get("step") or st.get("kind") or (st.get("proposal") or {}).get("kind") or "step"
                print("   -", label, st.get("status"), str(st.get("detail") or st.get("reason") or "")[:100])
        entries = c.get("/api/audit?limit=1000", headers=admin).json()
        entries = entries.get("data", entries) if isinstance(entries, dict) else entries
        entries = entries.get("entries", entries) if isinstance(entries, dict) else entries
        ids: list[str] = []
        for e in entries:
            for i in (e.get("stripe") or {}).get("ids", []):
                if i not in ids:
                    ids.append(i)
        print("\nStripe/provider object ids:", ", ".join(ids) or "none")
        v = c.get("/api/audit/verify", headers=admin).json()
        v = v.get("data", v)
        print("audit verify:", v, f"({len(entries)} entries)")
        if not v.get("ok"):
            rc = 1
    finally:
        if proc:
            proc.terminate()
            proc.wait(10)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
