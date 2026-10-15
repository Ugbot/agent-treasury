"""Agent + scenario routes. Integrator: call `configure(ledger, gateway)` once, then mount `router`.

POST /agent/step                 one agent step for a scenario-run mission
POST /scenarios/{id}/run         run (or resume with approvals) one of the four scenarios
GET  /agent/status               agent mode (LLM or GOAP-only) stated explicitly
GET  /scenarios/{id}/runs/{rid}  current view of a run
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .agent import GOAP_ONLY_NOTE, MODEL, Agent
from .gateway import GatewayError, LiveModeError, MockGateway
from .ledger import Ledger
from .scenarios import (
    SCENARIO_IDS,
    TITLES,
    ScenarioError,
    ScenarioRunner,
    build_runtime,
    step_once,
)

router = APIRouter(tags=["agent"])
_runner: ScenarioRunner | None = None


def configure(ledger: Ledger, gateway, registry=None, fuel=None, refunds=None, llm_client=None,
              from_env: bool = True) -> ScenarioRunner:
    """Wire to the app's ledger/gateway/registry. Pass the same services the routers use."""
    global _runner
    if registry is None:
        from .routes_policies import get_registry
        registry = get_registry()
    _runner = ScenarioRunner(build_runtime(ledger, gateway, registry, fuel=fuel, refunds=refunds,
                                           llm_client=llm_client, from_env=from_env and llm_client is None))
    return _runner


def set_runner(r: ScenarioRunner | None) -> None:
    global _runner
    _runner = r


def get_runner() -> ScenarioRunner:
    if _runner is None:
        configure(Ledger(":memory:"), MockGateway())
    assert _runner is not None
    return _runner


class StepIn(BaseModel):
    run_id: str | None = None
    missionId: str | None = None  # frontend sends the mission id of the run
    agent: str = "auto"  # auto|llm|goap
    use_llm: bool | None = None


class RunIn(BaseModel):
    run_id: str | None = None
    approvals: list[str] = []
    use_llm: bool = True
    mode: str | None = None  # informational: the gateway decides mock vs stripe_test, a mismatch is rejected


@router.get("/agent/status")
def status():
    rt = get_runner().rt
    a = Agent(rt.audit, rt.registry, client=rt.llm_client, mode=rt.mode).status()
    return {**a, "gateway_mode": rt.mode, "model_id": MODEL, "goap_only_note": GOAP_ONLY_NOTE,
            "scenarios": [{"id": i, "title": TITLES[i]} for i in SCENARIO_IDS]}


@router.post("/agent/step")
def agent_step(b: StepIn):
    r = get_runner()
    run = r.get(b.run_id) if b.run_id else next(
        (x for x in r.runs.values() if x.mission_id == b.missionId), None)
    if run is None:
        raise HTTPException(404, "unknown run: start one with POST /scenarios/{id}/run")
    rt = r.rt
    rt.use_llm = b.use_llm if b.use_llm is not None else b.agent != "goap"
    try:
        step = step_once(rt, run)
    except ScenarioError as e:
        raise HTTPException(409, str(e)) from e
    except LiveModeError as e:
        raise HTTPException(409, str(e)) from e
    except GatewayError as e:
        raise HTTPException(502, str(e)) from e
    return {"label": "simulated", "mode": rt.mode, "step": step, "run": run.view(rt)}


@router.post("/scenarios/{scenario_id}/run")
def run_scenario(scenario_id: str, b: RunIn | None = None):
    b = b or RunIn()
    r = get_runner()
    if b.mode and b.mode != r.rt.mode:
        raise HTTPException(409, f"server gateway is in {r.rt.mode} mode; cannot run in {b.mode}. "
                                 "Set STRIPE_SECRET_KEY (sk_test_) for stripe_test.")
    r.rt.use_llm = b.use_llm
    try:
        return r.run(scenario_id, b.run_id, b.approvals)
    except ScenarioError as e:
        raise HTTPException(404 if "unknown scenario" in str(e) else 409, str(e)) from e
    except LiveModeError as e:
        raise HTTPException(409, str(e)) from e
    except GatewayError as e:
        raise HTTPException(502, str(e)) from e


@router.get("/scenarios/{scenario_id}/runs/{run_id}")
def get_run(scenario_id: str, run_id: str):
    r = get_runner()
    run = r.get(run_id)
    if run is None or run.scenario != scenario_id:
        raise HTTPException(404, "unknown run")
    return run.view(r.rt)
