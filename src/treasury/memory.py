"""Policy memory behind an interface. Cognee is optional; never claims a read or write that did not happen.

Cognee 1.x has two documented paths (see cognee.serve docstring):
  * remote / Cognee Cloud: COGNEE_SERVICE_URL (tenant URL) + COGNEE_API_KEY. The key authenticates
    against that instance; remember/recall are routed to it and no LLM key is needed locally.
  * local: no service URL; Cognee runs in-process with its own stores and needs LLM_API_KEY
    (plus optional LLM_PROVIDER / LLM_MODEL, read by Cognee itself).
A COGNEE_API_KEY alone is NOT enough (it would trigger an interactive device login), so it is
reported as unconfigured with setup text rather than guessed at.

The audit log is canonical. Cognee is written from it (decisions) and from the policy registry
(policy versions); every Cognee write/read, including "unavailable", is appended to the audit log
with actor 'cognee'.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

SETUP_MESSAGE = (
    "Cognee is not configured. Install the 'cognee' package and set either "
    "COGNEE_SERVICE_URL + COGNEE_API_KEY (Cognee Cloud / remote instance) or LLM_API_KEY "
    "(local Cognee) to enable policy memory. Deterministic policy evaluation still uses the "
    "local validated snapshot and the audit log."
)
DEFAULT_DATASET = "agent_treasury"
_CLAUSE_ID = re.compile(r"\b[A-Z][A-Z0-9]{1,}(?:-[A-Z0-9]+)*-\d+\b")
_POLICY_VER = re.compile(r"\bpolicy[ :=]+([A-Za-z0-9_.-]+)\s+v(?:ersion)?[ :=]?(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class Readiness:
    ready: bool
    backend: str
    message: str
    mode: str = "none"  # none | cloud | local
    configured: bool = False
    reachable: bool | None = None  # None = never probed
    last_write: dict | None = None  # {"at", "status", "message", "ref"}


@dataclass(frozen=True)
class MemoryWrite:
    ok: bool
    status: str  # written | unavailable | error
    message: str
    source_ref: str | None = None  # only set when ok


@dataclass
class Explanation:
    source: str  # 'cognee' | 'audit_log'
    mission_id: str
    question: str
    clause_ids: list[str] = field(default_factory=list)
    policy_version: dict | None = None  # {"id","version","hash"}
    evidence: list[str] = field(default_factory=list)
    audit_seqs: list[int] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {"source": self.source, "mission_id": self.mission_id, "question": self.question,
                "clause_ids": self.clause_ids, "policy_version": self.policy_version,
                "evidence": self.evidence, "audit_seqs": self.audit_seqs, "note": self.note}


class PolicyMemory(Protocol):
    def readiness(self) -> Readiness: ...
    def write_policy(self, policy_id: str, version: int, content_hash: str, text: str) -> MemoryWrite: ...


# ---------------------------------------------------------------- Cognee client seam

class CogneeClient(Protocol):
    """Minimal async surface; the real one wraps the cognee SDK, tests use a fake."""
    async def health(self) -> bool: ...
    async def remember(self, text: str, dataset: str) -> Any: ...
    async def recall(self, query: str, dataset: str) -> Any: ...


def cognee_mode() -> str:
    if os.environ.get("COGNEE_SERVICE_URL") and os.environ.get("COGNEE_API_KEY"):
        return "cloud"
    if os.environ.get("LLM_API_KEY"):
        return "local"
    return "none"


class SdkCogneeClient:
    """Wraps the installed cognee SDK. Mode comes from env (see module docstring)."""

    def __init__(self, mode: str | None = None):
        self.mode = mode or cognee_mode()
        self._connected = False

    async def _connect(self):
        import cognee  # type: ignore
        if self.mode == "cloud" and not self._connected:
            await cognee.serve(url=os.environ["COGNEE_SERVICE_URL"], api_key=os.environ["COGNEE_API_KEY"])
            self._connected = True  # serve() raises if the key is rejected
        return cognee

    async def health(self) -> bool:
        await self._connect()  # cloud: raises on bad key / unreachable; local: import only
        return True

    async def remember(self, text: str, dataset: str) -> Any:
        return await (await self._connect()).remember(text, dataset_name=dataset)

    async def recall(self, query: str, dataset: str) -> Any:
        return await (await self._connect()).recall(query, datasets=[dataset])


def _flatten(results: Any) -> list[str]:
    """Reduce whatever recall returned (str, dict, pydantic, nested lists) to text snippets."""
    out: list[str] = []

    def walk(x):
        if x is None:
            return
        if isinstance(x, str):
            if x.strip():
                out.append(x.strip())
        elif isinstance(x, dict):
            for k in ("text", "content", "answer", "search_result", "result"):
                if k in x:
                    walk(x[k])
                    return
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
        elif hasattr(x, "model_dump"):
            walk(x.model_dump())
        else:
            walk(str(x))

    walk(results)
    return out


def _run(coro, timeout: float = 120.0):
    """Run an async call from sync code, also when an event loop is already running."""
    async def guarded():
        return await asyncio.wait_for(coro, timeout)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(guarded())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(guarded())).result()


# ---------------------------------------------------------------- audit helpers

def _decision_text(e: dict) -> str:
    p = e.get("policy") or {}
    clauses = p.get("clauses") or []
    parts = []
    for c in clauses:
        if isinstance(c, dict):
            cid = c.get("id") or c.get("clause_id") or "?"
            res = c.get("verdict") or c.get("result") or c.get("ok")
            ev = c.get("evidence") or c.get("reason") or c.get("message") or ""
            parts.append(f"clause {cid} -> {res} {ev}".strip())
        else:
            parts.append(f"clause {c}")
    stripe = e.get("stripe") or {}
    return (f"Decision audit#{e['seq']} mission {e.get('mission_id')} actor {e['actor']}: {e.get('summary', '')}. "
            f"policy {p.get('id', '?')} v{p.get('version', '?')} hash {p.get('hash', '?')} verdict {p.get('verdict', '?')}. "
            + ("; ".join(parts) + ". " if parts else "")
            + f"Evidence: {e.get('payload') or {}}. Stripe: {stripe.get('ids', []) if stripe else []}.")


def _audit_explanation(audit, mission_id: str, question: str, note: str) -> Explanation:
    ex = Explanation("audit_log", mission_id, question, note=note)
    if audit is None:
        ex.note = note + " No audit log attached."
        return ex
    for e in audit.query(mission_id=mission_id, kind="decision"):
        ex.audit_seqs.append(e["seq"])
        p = e.get("policy") or {}
        if p.get("id"):
            ex.policy_version = {"id": p.get("id"), "version": p.get("version"), "hash": p.get("hash")}
        for c in p.get("clauses") or []:
            cid = c.get("id") or c.get("clause_id") if isinstance(c, dict) else str(c)
            if cid and cid not in ex.clause_ids:
                ex.clause_ids.append(cid)
        ex.evidence.append(_decision_text(e))
    if not ex.audit_seqs:
        ex.note += " No decision entries found for this mission in the audit log."
    return ex


# ---------------------------------------------------------------- memories

class UnconfiguredMemory:
    backend = "none"

    def __init__(self, audit=None):
        self.audit = audit

    def readiness(self) -> Readiness:
        return Readiness(False, self.backend, SETUP_MESSAGE)

    def status(self, probe: bool = False) -> dict:
        r = self.readiness()
        return {"ready": r.ready, "backend": r.backend, "message": r.message, "mode": r.mode,
                "configured": False, "reachable": None, "last_write": None}

    def write_policy(self, policy_id, version, content_hash, text) -> MemoryWrite:
        return MemoryWrite(False, "unavailable", SETUP_MESSAGE, None)

    def sync_decisions(self, mission_id: str | None = None) -> dict:
        return {"written": 0, "skipped": 0, "failed": 0, "status": "unavailable", "message": SETUP_MESSAGE}

    def explain(self, mission_id: str, question: str) -> Explanation:
        return _audit_explanation(self.audit, mission_id, question, "Cognee unavailable: " + SETUP_MESSAGE)


class CogneeMemory:
    backend = "cognee"

    def __init__(self, dataset: str = DEFAULT_DATASET, *, client: CogneeClient | None = None, audit=None,
                 timeout: float = 120.0):
        self.dataset = dataset
        self.audit = audit
        self.timeout = timeout
        self._client = client
        self._reachable: bool | None = None
        self._last_write: dict | None = None

    def attach_audit(self, audit) -> None:
        self.audit = audit

    # -- config
    @staticmethod
    def _installed() -> bool:
        return importlib.util.find_spec("cognee") is not None

    @staticmethod
    def _configured() -> bool:
        return cognee_mode() != "none"

    @property
    def client(self) -> CogneeClient:
        if self._client is None:
            self._client = SdkCogneeClient()
        return self._client

    def _unavailable_reason(self) -> str | None:
        if self._client is not None and not isinstance(self._client, SdkCogneeClient):
            return None  # injected client (tests / custom deployments)
        if not self._installed() or not self._configured():
            return SETUP_MESSAGE
        return None

    def readiness(self) -> Readiness:
        why = self._unavailable_reason()
        injected = self._client is not None and not isinstance(self._client, SdkCogneeClient)
        mode = "injected" if injected else cognee_mode()
        if why:
            return Readiness(False, self.backend, why, mode="none", configured=False,
                             reachable=self._reachable, last_write=self._last_write)
        msg = ("Cognee configured; reachable" if self._reachable else
               "Cognee configured; not reachable at last probe" if self._reachable is False else
               "Cognee configured (reachability not probed yet)")
        return Readiness(self._reachable is not False, self.backend, msg, mode=mode, configured=True,
                         reachable=self._reachable, last_write=self._last_write)

    def status(self, probe: bool = False) -> dict:
        """For the UI: configured / reachable / last write. probe=True performs a live check."""
        if probe and self._unavailable_reason() is None:
            try:
                self._reachable = bool(_run(self.client.health(), min(self.timeout, 30)))
            except Exception:  # noqa: BLE001
                self._reachable = False
        r = self.readiness()
        return {"ready": r.ready, "backend": r.backend, "message": r.message, "mode": r.mode,
                "configured": r.configured, "reachable": r.reachable, "last_write": r.last_write}

    # -- audit
    def _audit(self, kind: str, summary: str, **kw) -> None:
        if self.audit is not None:
            self.audit.append("cognee", kind, summary, **kw)

    def _note_write(self, w: MemoryWrite) -> None:
        self._last_write = {"at": datetime.now(UTC).isoformat(), "status": w.status,
                            "message": w.message, "ref": w.source_ref}

    # -- writes
    def _write(self, text: str, ref: str, kind_summary: str, mission_id: str | None = None,
               payload: dict | None = None) -> MemoryWrite:
        why = self._unavailable_reason()
        if why:
            w = MemoryWrite(False, "unavailable", why, None)
        else:
            try:
                _run(self.client.remember(text, self.dataset), self.timeout)
                self._reachable = True
                w = MemoryWrite(True, "written", "ingested and cognified", ref)
            except Exception as exc:  # noqa: BLE001
                self._reachable = False
                w = MemoryWrite(False, "error", f"Cognee write failed: {type(exc).__name__}", None)
        self._note_write(w)
        self._audit("memory.write", f"{kind_summary}: {w.status}", mission_id=mission_id,
                    payload={**(payload or {}), "status": w.status, "message": w.message,
                             "ref": w.source_ref, "dataset": self.dataset})
        return w

    def write_policy(self, policy_id: str, version: int, content_hash: str, text: str) -> MemoryWrite:
        body = (f"Policy {policy_id} v{version} (hash {content_hash}). Policy text with clause ids:\n{text}")
        return self._write(body, f"cognee://{self.dataset}/{policy_id}/v{version}#{content_hash}",
                           f"policy {policy_id} v{version}",
                           payload={"policy_id": policy_id, "version": version, "hash": content_hash})

    def _already_written(self) -> set[int]:
        if self.audit is None:
            return set()
        return {e["payload"]["audit_seq"] for e in self.audit.query(kind="memory.write", actor="cognee", limit=100000)
                if e.get("payload") and e["payload"].get("status") == "written" and "audit_seq" in e["payload"]}

    def sync_decisions(self, mission_id: str | None = None) -> dict:
        """Write decision audit entries not yet in Cognee. Idempotent via memory.write audit rows."""
        if self.audit is None:
            return {"written": 0, "skipped": 0, "failed": 0, "status": "unavailable",
                    "message": "No audit log attached; nothing to sync."}
        done = self._already_written()
        n = {"written": 0, "skipped": 0, "failed": 0}
        last = "ok"
        for e in self.audit.query(kind="decision", mission_id=mission_id, limit=100000):
            if e["seq"] in done:
                n["skipped"] += 1
                continue
            w = self._write(_decision_text(e), f"cognee://{self.dataset}/decision/{e['seq']}",
                            f"decision audit#{e['seq']}", mission_id=e.get("mission_id"),
                            payload={"audit_seq": e["seq"]})
            n["written" if w.ok else "failed"] += 1
            last = w.status
            if w.status == "unavailable":
                break  # same answer for every remaining entry; one audit row is enough
        return {**n, "status": last, "message": self._last_write["message"] if self._last_write else ""}

    # -- reads
    def explain(self, mission_id: str, question: str) -> Explanation:
        why = self._unavailable_reason()
        snippets: list[str] = []
        status, msg = "unavailable", why or ""
        if not why:
            try:
                snippets = _flatten(_run(self.client.recall(f"{question} (mission {mission_id})", self.dataset),
                                         self.timeout))
                self._reachable = True
                status = "ok" if snippets else "empty"
                msg = "" if snippets else "Cognee returned nothing for this question."
            except Exception as exc:  # noqa: BLE001
                self._reachable = False
                status, msg = "error", f"Cognee read failed: {type(exc).__name__}"
        self._audit("memory.read", f"explain {mission_id}: {status}", mission_id=mission_id,
                    payload={"question": question, "status": status, "message": msg, "hits": len(snippets),
                             "dataset": self.dataset})
        if not snippets:
            return _audit_explanation(self.audit, mission_id, question,
                                      f"{msg} Answer is from the audit log, not Cognee.")
        text = "\n".join(snippets)
        clause_ids = list(dict.fromkeys(_CLAUSE_ID.findall(text)))
        pv = _POLICY_VER.search(text)
        ex = Explanation("cognee", mission_id, question, clause_ids=clause_ids,
                         evidence=[s[:600] for s in snippets[:5]])
        if pv:
            h = re.search(r"hash\s+([0-9a-f]{8,64})", text)
            ex.policy_version = {"id": pv.group(1), "version": int(pv.group(2)), "hash": h.group(1) if h else None}
        ex.audit_seqs = [e["seq"] for e in (self.audit.query(mission_id=mission_id, kind="decision")
                                            if self.audit else [])]
        if not clause_ids:
            ex.note = "Cognee returned text but no clause ids were recognised in it."
        return ex


def default_memory(audit=None) -> PolicyMemory:
    return CogneeMemory(audit=audit)
