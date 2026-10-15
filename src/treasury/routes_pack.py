"""GET /audit/pack?mission=<id>: downloadable audit pack zip (admin/approver only, demo auth).

Mount: app.include_router(routes_pack.router) (and under /api), like routes_audit.
"""
from __future__ import annotations

import contextlib
import re

from fastapi import APIRouter, Request
from fastapi.responses import Response

from .audit_pack import build_pack
from .routes_audit import AuthError, _err, caller_of, get_audit

router = APIRouter(tags=["audit"])


@router.get("/audit/pack")
def audit_pack(request: Request, mission: str = ""):
    try:
        caller = caller_of(request)
    except AuthError as e:
        return e.response
    if caller.role not in ("admin", "approver"):
        return _err(403, "forbidden", "audit packs require an admin or approver role")
    if not mission:
        return _err(400, "mission_required", "pass ?mission=<id>")
    log = get_audit(request.app.state)
    if not log.query(mission_id=mission, limit=1):
        from .routes_explain import (
            _resolve_mission,  # a payment/operation id resolves to its mission
        )
        with contextlib.suppress(Exception):
            mission = _resolve_mission(request, log, mission)
    if not log.query(mission_id=mission, limit=1):
        return _err(404, "not_found", f"no audit entries for mission {mission}")
    log.append("approver" if caller.role == "approver" else "system", "audit.pack",
               f"audit pack downloaded by {caller.role}", mission_id=mission, party=caller.party_id,
               payload={"role": caller.role})
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", mission)
    return Response(build_pack(log, mission), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="audit-pack-{safe}.zip"'})
