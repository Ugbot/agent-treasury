"""AI-assisted policy authoring routes ({data}/{error} envelope like routes_console).

The model drafts; a human validates, dry-runs and publishes. /author/draft never publishes or attaches."""
from __future__ import annotations

from fastapi import APIRouter, Request

from . import policy_author as pa
from .audit import redact
from .decision import APPROVER_ROLES, org_currency
from .policies import ConflictError, MandatoryError, PolicyError
from .routes_console import actor_from, err, ok
from .routes_policies import _http, get_registry

router = APIRouter(prefix="/author", tags=["author"])


async def _body(request: Request) -> dict | None:
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        return None
    return b if isinstance(b, dict) else None


def _bad(msg: str):
    return err(422, "bad_request", msg)


@router.get("/clause-kinds")
def clause_kinds():
    return ok({"kinds": pa.CLAUSE_KINDS, "inputs": pa.KNOWN_INPUTS})


@router.post("/draft")
async def draft(request: Request):
    b = await _body(request)
    if b is None or not isinstance(b.get("description"), str) or not b["description"].strip():
        return _bad("description (non-empty string) is required")
    if len(b["description"]) > 4000:
        return _bad("description must be at most 4000 characters")
    base = b.get("base_policy_id")
    if base is not None and not isinstance(base, str):
        return _bad("base_policy_id must be a string")
    try:
        out = pa.draft(get_registry(), b["description"], base)
    except pa.DraftError as e:
        return err(e.status, e.code, str(e), errors=e.errors)
    audit = getattr(request.app.state, "audit", None)
    if out.get("available") and audit is not None:
        # recorded as the agent; the draft is a proposal, not a policy
        audit.append("agent", "policy.draft", f"AI drafted policy {(out.get('draft') or {}).get('policy_id', '-')}",
                     payload={"prompt": redact(b["description"]), "model": out.get("model"),
                              "base_policy_id": base, "draft": out.get("draft"),
                              "unsupported": out.get("unsupported"), "ai_drafted": True})
    return ok(out)


def _parse_policy(b: dict | None):
    if b is None:
        return None, "JSON body required"
    pid, title, cl = b.get("policy_id"), b.get("title"), b.get("clauses")
    if not isinstance(pid, str) or not pa.POLICY_ID.fullmatch(pid):
        return None, "policy_id must be a lowercase slug"
    if not isinstance(title, str) or not title.strip():
        return None, "title is required"
    if len(title.strip()) > pa.MAX_TITLE:
        return None, f"title must be at most {pa.MAX_TITLE} characters"
    if not isinstance(cl, list):
        return None, "clauses must be a list"
    return (pid, title.strip(), cl), None


@router.post("/validate")
async def validate(request: Request):
    b = await _body(request)
    parsed, msg = _parse_policy(b)
    if parsed is None:
        return _bad(msg)
    pid, _title, cl = parsed
    reg = get_registry()
    errors = pa.validate_clauses(cl)
    valid_shape = not errors
    weakens = pa.weakenings(reg, pid, cl) if valid_shape else []
    mandatory = pa.is_mandatory(reg, pid)
    if mandatory:
        errors += [{"clause": w.split()[1] if w.startswith("clause ") else None, "message": f"weakens mandatory policy: {w}"}
                   for w in weakens]
    exp = b.get("expected_version")
    if exp is not None:
        cur = len(reg._versions.get(pid, []))
        if exp != cur:
            errors.append({"clause": None, "message": f"stale: expected version {exp}, current is {cur}"})
    cur_code = org_currency(reg) or "usd"
    sentences = []
    if valid_shape:
        for c in cl:
            text, v = pa.sentence(c, cur_code)
            sentences.append({"id": c["id"], "text": text, "verdict_if_unmet": v})
    return ok({"valid": not errors, "errors": errors, "weakens": weakens,
               "diff": pa.diff(reg, pid, cl) if valid_shape else {"added": [], "removed": [], "changed": []},
               "sentences": sentences, "mandatory": mandatory,
               "current_version": pa.current_version(reg, pid)})


@router.post("/dry-run")
async def dry_run(request: Request):
    b = await _body(request)
    if b is None or not isinstance(b.get("clauses"), list):
        return _bad("clauses must be a list")
    errors = pa.validate_clauses(b["clauses"])
    if errors:
        return err(422, "invalid_policy", "; ".join(e["message"] for e in errors), errors=errors)
    sits = b.get("situations")
    if sits is not None and (not isinstance(sits, list) or not all(isinstance(s, str) for s in sits)):
        return _bad("situations must be a list of strings")
    pid = b.get("policy_id") if isinstance(b.get("policy_id"), str) else "draft"
    try:
        return ok({"results": pa.dry_run(get_registry(), b["clauses"], sits, pid)})
    except ValueError as e:
        return _bad(str(e))
    except PolicyError as e:
        return err(422, "invalid_policy", str(e))


@router.post("/publish")
async def publish(request: Request):
    b = await _body(request)
    parsed, msg = _parse_policy(b)
    if parsed is None:
        return _bad(msg)
    pid, title, cl = parsed
    exp = b.get("expected_version")
    if not isinstance(exp, int) or isinstance(exp, bool) or exp < 0:
        return _bad("expected_version (integer) is required")
    ai = b.get("ai_drafted")
    if not isinstance(ai, bool):
        return _bad("ai_drafted (boolean) is required")
    actor = actor_from(request, b)
    if actor.role not in APPROVER_ROLES:
        return err(403, "forbidden", f"publishing a policy requires an approver role (got {actor.role!r})")
    errors = pa.validate_clauses(cl)
    if errors:
        return err(422, "invalid_policy", "; ".join(e["message"] for e in errors), errors=errors)
    reg = get_registry()
    if pa.is_mandatory(reg, pid):
        weak = pa.weakenings(reg, pid, cl)
        if weak:
            return err(403, "mandatory_weakened", f"mandatory policy cannot be weakened: {weak[0]}", weakens=weak)
    try:
        bundle = reg.publish(pid, title, pa.to_clauses(cl), exp)
    except ConflictError as e:
        return err(409, "stale_version", str(e))
    except MandatoryError as e:
        return err(403, "mandatory_weakened", str(e))
    except PolicyError as e:
        h = _http(e)
        return err(h.status_code, "policy_error", str(e))
    audit = getattr(request.app.state, "audit", None)
    if audit is not None:
        prompt = redact(b["prompt"]) if isinstance(b.get("prompt"), str) else None
        model = b.get("model") if isinstance(b.get("model"), str) else None
        audit.append("approver", "policy.authored",
                     f"policy {pid} v{bundle.version} published by {actor.id} (ai_drafted={ai})",
                     party=actor.id,
                     payload={"policy_id": pid, "version": bundle.version, "content_hash": bundle.content_hash,
                              "ai_drafted": ai, "prompt": prompt, "model": model, "approver": actor.id,
                              "role": actor.role},
                     policy={"id": pid, "version": bundle.version, "hash": bundle.content_hash,
                             "ai_drafted": ai, "clauses": [c.id for c in bundle.clauses]})
    return ok({**bundle.to_dict(), "ai_drafted": ai})
