"""Policy bundle / attachment / effective-decision routes. Integrator mounts `router`."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .policies import (
    ConflictError,
    MandatoryError,
    NotFoundError,
    PolicyError,
    PolicyRegistry,
    TargetLockedError,
    default_registry,
)
from .policy import Clause

router = APIRouter(prefix="/policies", tags=["policies"])
_registry: PolicyRegistry | None = None


def get_registry() -> PolicyRegistry:
    global _registry
    if _registry is None:
        _registry = default_registry()
    return _registry


def set_registry(reg: PolicyRegistry | None) -> None:
    global _registry
    _registry = reg


def _http(exc: PolicyError) -> HTTPException:
    code = 400
    if isinstance(exc, (ConflictError, TargetLockedError)):
        code = 409
    elif isinstance(exc, MandatoryError):
        code = 403
    elif isinstance(exc, NotFoundError):
        code = 404
    return HTTPException(code, str(exc))


class ClauseIn(BaseModel):
    id: str
    prose: str
    kind: str
    params: dict = {}


class PublishIn(BaseModel):
    title: str
    clauses: list[ClauseIn]
    expected_version: int = 0


class AttachIn(BaseModel):
    target_type: str
    target_id: str
    policy_id: str
    policy_version: int
    expected_version: int
    mandatory: bool = False


class DecideIn(BaseModel):
    target_type: str = "payment"
    target_id: str
    inputs: dict = {}
    input_revision: int
    amount_minor: int = Field(gt=0)
    inherited: list[tuple[str, str]] = []


def _clauses(c): return [Clause(x.id, x.prose, x.kind, x.params) for x in c]  # type: ignore[arg-type]


@router.get("/memory/status")
def memory_status(probe: bool = False):
    mem = get_registry().memory
    if hasattr(mem, "status"):
        return mem.status(probe=probe)
    r = mem.readiness()
    return {"ready": r.ready, "backend": r.backend, "message": r.message, "mode": getattr(r, "mode", "none"),
            "configured": getattr(r, "configured", False), "reachable": getattr(r, "reachable", None),
            "last_write": getattr(r, "last_write", None)}


class ExplainIn(BaseModel):
    mission_id: str
    question: str = "Why was this paid?"


@router.post("/memory/explain")
def memory_explain(body: ExplainIn):
    mem = get_registry().memory
    if not hasattr(mem, "explain"):
        raise HTTPException(501, "memory backend has no explain()")
    return mem.explain(body.mission_id, body.question).to_dict()


@router.post("/memory/sync")
def memory_sync(mission_id: str | None = None):
    mem = get_registry().memory
    if not hasattr(mem, "sync_decisions"):
        raise HTTPException(501, "memory backend has no sync_decisions()")
    return mem.sync_decisions(mission_id)


@router.get("")
def list_policies():
    return [b.to_dict() for b in get_registry().list()]


@router.post("/{policy_id}/versions", status_code=201)
def publish(policy_id: str, body: PublishIn):
    try:
        return get_registry().publish(policy_id, body.title, _clauses(body.clauses), body.expected_version).to_dict()
    except PolicyError as e:
        raise _http(e)


@router.get("/{policy_id}")
def get_policy(policy_id: str, version: int | None = None):
    try:
        return get_registry().get(policy_id, version).to_dict()
    except PolicyError as e:
        raise _http(e)


@router.post("/attachments", status_code=201)
def attach(body: AttachIn):
    try:
        reg = get_registry()
        a = reg.attach(body.target_type, body.target_id, body.policy_id, body.policy_version,
                       body.expected_version, body.mandatory)
        return {**a.__dict__, "target_version": reg.target_version(a.target_type, a.target_id)}
    except PolicyError as e:
        raise _http(e)


@router.delete("/attachments/{attachment_id}")
def detach(attachment_id: str, expected_version: int):
    try:
        return {"target_version": get_registry().detach(attachment_id, expected_version)}
    except PolicyError as e:
        raise _http(e)


@router.get("/targets/{target_type}/{target_id}/effective")
def effective(target_type: str, target_id: str):
    reg = get_registry()
    return {
        "target_version": reg.target_version(target_type, target_id),
        "attachments": [a.__dict__ for a in reg.effective_attachments([(target_type, target_id)])],
    }


@router.post("/decide")
def decide(body: DecideIn):
    d = get_registry().decide(body.target_type, body.target_id, body.inputs, body.input_revision,
                              body.amount_minor, [tuple(x) for x in body.inherited])
    return {**d.__dict__, "results": list(d.results), "attachments": [list(a) for a in d.attachments],
            "reasons": list(d.reasons)}
