"""HTTP API. Backend owns policy and money authority; the browser only sends inputs."""
from __future__ import annotations

import json
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .ledger import Ledger
from .policy import DEFAULT_POLICY, evaluate


class MissionIn(BaseModel):
    id: str
    amount_minor: int = Field(gt=0)
    currency: str = "GBP"


class EventIn(BaseModel):
    event_id: str
    expected_revision: int
    inputs: dict


def create_app(ledger: Ledger | None = None) -> FastAPI:
    app = FastAPI(title="Agent Treasury")
    led = ledger or Ledger(os.environ.get("TREASURY_DB", "treasury.sqlite"))

    def decide(mission_id: str) -> dict:
        m = led.mission(mission_id)
        if m is None:
            raise HTTPException(404, "unknown mission")
        d = evaluate(DEFAULT_POLICY, json.loads(m["inputs"]), m["revision"], m["amount_minor"])
        body = {**d.__dict__, "results": list(d.results), "mode": "mock"}
        led.record_decision(mission_id, m["revision"], body)
        if d.verdict == "allow":
            # TODO(ATREAS-2): move into a DBOS workflow; Stripe test mode replaces mock.
            led.pay_once(f"pay:{mission_id}", mission_id, m["amount_minor"], m["currency"], "mock", f"mock_{mission_id}")
        return body

    @app.post("/missions")
    def create(m: MissionIn):
        led.create_mission(m.id, m.amount_minor, m.currency)
        return decide(m.id)

    @app.post("/missions/{mission_id}/events")
    def event(mission_id: str, e: EventIn):
        try:
            led.apply_event(e.event_id, mission_id, e.expected_revision, e.inputs)
        except KeyError:
            raise HTTPException(404, "unknown mission")
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return decide(mission_id)

    return app


app = create_app
