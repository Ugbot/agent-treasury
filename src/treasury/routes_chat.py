"""Chat/draft routes. Integrator mounts `router`. Context comes from an injectable provider."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from .chat import ChatContext, ChatError, Unavailable, get_service

router = APIRouter(tags=["chat"])
_context_provider: Callable[[str], ChatContext | None] = lambda mission_id: None


def set_context_provider(fn: Callable[[str], ChatContext | None]) -> None:
    """Integrator hook: build context from persisted decisions and retrieved evidence."""
    global _context_provider
    _context_provider = fn


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandId: str
    text: str
    history: list[dict] = []


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandId: str
    recipientRole: str
    intent: str


class DraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    expectedRevision: int


def _ctx(mission_id: str) -> ChatContext:
    ctx = _context_provider(mission_id)
    if ctx is None:
        raise HTTPException(404, "mission context not found")
    return ctx


@router.get("/chat/status")
def status() -> dict:
    return get_service().status()


@router.post("/missions/{mission_id}/chat/messages")
def chat(mission_id: str, body: ChatIn) -> dict:
    try:
        r = get_service().respond(_ctx(mission_id), body.history, body.text)
    except ChatError as exc:
        raise HTTPException(502, str(exc))
    if isinstance(r, Unavailable):
        return {"available": False, "reason": r.reason}
    return {"available": True, **asdict(r)}


@router.post("/missions/{mission_id}/drafts")
def draft(mission_id: str, body: DraftIn) -> dict:
    try:
        d = get_service().draft(mission_id, _ctx(mission_id), body.recipientRole, body.intent)
    except ChatError as exc:
        raise HTTPException(400 if "recipientRole" in str(exc) else 502, str(exc))
    if isinstance(d, Unavailable):
        return {"available": False, "reason": d.reason}
    return {"available": True, **asdict(d)}


@router.patch("/drafts/{draft_id}")
def patch_draft(draft_id: str, body: DraftPatch) -> dict:
    try:
        return asdict(get_service().store.update(draft_id, body.text, body.expectedRevision))
    except KeyError:
        raise HTTPException(404, "draft not found")
    except ChatError as exc:
        raise HTTPException(409, str(exc))
