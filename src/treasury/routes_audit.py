"""Audit read routes: filtered log, NDJSON export, chain verify, party-scoped views, SSE stream.

DEMO AUTH ONLY: the caller's identity comes from the X-Party-Role and X-Party-Id headers, which
anyone can set. This demonstrates role scoping; it is NOT production authentication. Replace with
real authn (signed tokens) before any non-demo use.

Mount: app.include_router(router) (and/or under /api). Needs `app.state.audit` (an AuditLog) or
`app.state.ledger` (a Ledger), see `get_audit`.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .audit import AuditLog

ROLES = ("sender", "receiver", "driver", "approver", "admin")
MAX_LIMIT = 1000
POLL_SECONDS = 0.5
HEARTBEAT_EVERY = 30  # polls (about 15 s)

router = APIRouter(tags=["audit"])


def get_audit(app_state) -> AuditLog:
    """Return the single AuditLog for this app, creating it on the ledger's connection once.

    Hook for api.py/wiring: `app.state.ledger = led` (or `app.state.audit = AuditLog(led.db)`).
    """
    log = getattr(app_state, "audit", None)
    if log is None:
        ledger = getattr(app_state, "ledger", None)
        if ledger is None:
            raise LookupError("no audit log: set app.state.ledger or app.state.audit")
        log = AuditLog(ledger.db)
        app_state.audit = log
    return log


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


@dataclass(frozen=True)
class Caller:
    role: str
    party_id: str | None

    @property
    def admin(self) -> bool:
        return self.role == "admin"


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.response = _err(status, code, message)


def caller_of(request: Request) -> Caller:
    role = (request.headers.get("X-Party-Role") or "").strip().lower()
    pid = (request.headers.get("X-Party-Id") or "").strip() or None
    if not role:
        raise AuthError(401, "auth_required", "send X-Party-Role (and X-Party-Id) headers (demo auth)")
    if role not in ROLES:
        raise AuthError(403, "bad_role", f"role must be one of {', '.join(ROLES)}")
    if role != "admin" and not pid:
        raise AuthError(401, "party_required", "X-Party-Id is required for non-admin roles")
    return Caller(role, pid)


def scope_entry(entry: dict, caller: Caller) -> dict:
    """Admin sees everything. Others get own-party entries with policy internals reduced."""
    if caller.admin:
        return entry
    e = dict(entry)
    if e.get("policy"):
        p = e["policy"]
        e["policy"] = {k: p[k] for k in ("id", "version", "verdict") if k in p}
    if e.get("stripe"):
        s = e["stripe"]
        e["stripe"] = {k: s[k] for k in ("ids", "livemode", "op") if k in s}
    if isinstance(e.get("payload"), dict):
        e["payload"] = {k: v for k, v in e["payload"].items() if not str(k).startswith("_")}
    e.pop("prev_hash", None)
    return e


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _effective_party(caller: Caller, requested: str | None) -> str | None:
    """Party filter to apply; raises AuthError if a non-admin asks for someone else's party."""
    if caller.admin:
        return requested
    if requested and requested != caller.party_id:
        raise AuthError(403, "forbidden_party", "you may only read your own party's entries")
    return caller.party_id


def _read(request: Request, party: str | None, mission, kind, actor, after, limit):
    caller = caller_of(request)
    eff = _effective_party(caller, party)
    log = get_audit(request.app.state)
    rows = log.query(mission_id=mission, party=eff, kind=kind, actor=actor, after=after, limit=_clamp(limit))
    return caller, log, [scope_entry(r, caller) for r in rows]


@router.get("/audit")
def list_audit(request: Request, mission: str | None = None, party: str | None = None,
               kind: str | None = None, actor: str | None = None, after: int = 0, limit: int = 500):
    try:
        _, _, rows = _read(request, party, mission, kind, actor, after, limit)
    except AuthError as e:
        return e.response
    return {"data": {"entries": rows, "next_after": rows[-1]["seq"] if rows else after}}


@router.get("/audit/export")
def export_audit(request: Request, mission: str | None = None, party: str | None = None,
                 kind: str | None = None, actor: str | None = None, after: int = 0, limit: int = MAX_LIMIT):
    try:
        _, _, rows = _read(request, party, mission, kind, actor, after, limit)
    except AuthError as e:
        return e.response
    body = "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows)
    return Response(body, media_type="application/x-ndjson",
                    headers={"Content-Disposition": 'attachment; filename="audit.ndjson"'})


@router.get("/audit/verify")
def verify_audit(request: Request, expected_head: str | None = None, expected_count: int | None = None):
    """Chain check. The head hash is shown in the UI so it can be anchored externally; pass it back
    as expected_head (and expected_count) to detect truncation of the log tail."""
    try:
        caller = caller_of(request)
    except AuthError as e:
        return e.response
    res = get_audit(request.app.state).verify(expected_head, expected_count)
    if not caller.admin:
        res = {k: v for k, v in res.items() if k not in ("head", "anchor_mismatch")}
    return {"data": res}


@router.get("/audit/party/{party}")
def party_audit(party: str, request: Request, mission: str | None = None, kind: str | None = None,
                after: int = 0, limit: int = 500):
    try:
        _, _, rows = _read(request, party, mission, kind, None, after, limit)
    except AuthError as e:
        return e.response
    return {"data": {"party": party, "entries": rows, "next_after": rows[-1]["seq"] if rows else after}}


def _sse(entry: dict) -> str:
    return f"id: {entry['seq']}\nevent: audit\ndata: {json.dumps(entry, sort_keys=True, default=str)}\n\n"


@router.get("/audit/stream")
async def stream_audit(request: Request, mission: str | None = None, party: str | None = None,
                       kind: str | None = None, after: int | None = None, follow: bool = True):
    """Server-sent events. Resume with ?after=<seq> or the Last-Event-ID header (the larger wins).

    Entries are emitted in seq order, each exactly once. follow=false drains the backlog and ends.
    """
    try:
        caller = caller_of(request)
        eff = _effective_party(caller, party)
        log = get_audit(request.app.state)
    except AuthError as e:
        return e.response
    last = after or 0
    try:
        last = max(last, int(request.headers.get("Last-Event-ID", 0)))
    except ValueError:
        pass

    async def gen():
        nonlocal last
        idle = 0
        yield "retry: 2000\n\n"
        while True:
            rows = log.query(mission_id=mission, party=eff, kind=kind, after=last, limit=MAX_LIMIT)
            for r in rows:
                if r["seq"] <= last:  # de-duplicate
                    continue
                last = r["seq"]
                yield _sse(scope_entry(r, caller))
            if not follow:
                return
            if not rows:
                idle += 1
                if idle % HEARTBEAT_EVERY == 0:
                    yield ": keep-alive\n\n"
                if await request.is_disconnected():
                    return
                await asyncio.sleep(POLL_SECONDS)
            else:
                idle = 0

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
