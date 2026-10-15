"""Customer job journey routes. Integrator mounts `router` (and under /api).

Hooks:
- app.state.jobs: optional prebuilt JobService; otherwise built lazily from app.state.ledger and the
  policy registry (routes_policies.get_registry()).
- app.state.gateway_mode: optional callable returning "mock" | "stripe_test" for labels.
- GET /policies/library is shadowed by routes_policies GET /policies/{policy_id} unless this router is
  included BEFORE routes_policies.router; the same data is always at GET /jobs/policy-library.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import jobs as J
from .policies import PolicyError
from .routes_policies import _http, get_registry

router = APIRouter(tags=["jobs"])


def get_service(request: Request) -> J.JobService:
    st = request.app.state
    svc = getattr(st, "jobs", None)
    if svc is None:
        mode = getattr(st, "gateway_mode", None) or (lambda: "mock")
        svc = J.JobService(st.ledger, get_registry, mode=mode)
        st.jobs = svc
    return svc


def _call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except J.JobError as e:
        raise HTTPException(e.status, e.message) from e
    except PolicyError as e:
        raise _http(e) from e


class AttachIn(BaseModel):
    policy_id: str
    version: int | None = None
    expected_version: int
    params: dict | None = None


class PreviewIn(BaseModel):
    situations: list[str] | None = None


@router.get("/jobs/policy-library")
@router.get("/policies/library")
def policy_library():
    return _call(J.library, get_registry())


@router.post("/jobs", status_code=201)
def create_job(body: J.JobIn, request: Request):
    return _call(get_service(request).create, body)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    return _call(get_service(request).get, job_id)


@router.get("/jobs/{job_id}/policies")
def job_policies(job_id: str, request: Request):
    return _call(get_service(request).effective, job_id)


@router.post("/jobs/{job_id}/policies")
def attach_policy(job_id: str, body: AttachIn, request: Request):
    return _call(get_service(request).attach, job_id, body.policy_id, body.version, body.expected_version, body.params)


@router.delete("/jobs/{job_id}/policies/{attachment_id}")
def detach_policy(job_id: str, attachment_id: str, expected_version: int, request: Request):
    return _call(get_service(request).detach, job_id, attachment_id, expected_version)


@router.post("/jobs/{job_id}/preview")
def preview(job_id: str, request: Request, body: PreviewIn | None = None):
    return _call(get_service(request).preview, job_id, body.situations if body else None)


@router.post("/jobs/{job_id}/dispatch")
def dispatch(job_id: str, request: Request):
    return _call(get_service(request).dispatch, job_id)


@router.get("/jobs/{job_id}/watch")
def watch(job_id: str, request: Request, after: int = 0):
    return _call(get_service(request).watch, job_id, after)
