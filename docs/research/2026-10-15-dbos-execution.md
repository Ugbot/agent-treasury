# DBOS Python feasibility for agent-treasury

Researched 15 October 2026, official docs only. No application implementation or API mutations. Proposed stack: basic GOAP planner, Cognee policy memory, DBOS durable execution, Stripe.

## Minimal runtime

Install `dbos` and `fastapi[standard]`. Current Python docs support an ordinary synchronous FastAPI application, initializing `DBOS(config=config)`, then `DBOS.launch()`, then `uvicorn.run(app, ...)`. No orchestration server is required for a local demo. Default system database is SQLite; an explicit persistent URL is preferable so the launch directory cannot silently choose another database. [Programming guide](https://docs.dbos.dev/python/programming-guide)

```python
from dbos import DBOS, DBOSConfig
from fastapi import FastAPI
import uvicorn

app = FastAPI()

# Register workflow/step functions before launch.
if __name__ == "__main__":
    config: DBOSConfig = {
        "name": "agent-treasury",
        "application_version": "0.1.0",
        "system_database_url": "sqlite:///data/dbos.sqlite",
    }
    DBOS(config=config)
    DBOS.launch()
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

Create the `data/` parent directory first. SQLite supports a single-server prototype; use Postgres for distributed deployments. App data can live in a separate SQLite/Postgres datasource. [Database connections](https://docs.dbos.dev/python/tutorials/database-connection)

SQLite always polls for message/event notifications. Default interval is one second; `notification_listener_polling_interval_sec` can reduce this for the demo. Pin a tested SDK version; avoid changing workflow implementation while old executions are pending without applying DBOS versioning/patching guidance. [Configuration](https://docs.dbos.dev/python/reference/configuration)

## Exact synchronous API shape

```python
@DBOS.step(retries_allowed=True, max_attempts=3,
           interval_seconds=1.0, backoff_rate=2.0)
def external_operation(operation_id: str):
    ...

@DBOS.workflow()
def mission(mission_id: str):
    result = external_operation(f"{mission_id}:action:1")
    event = DBOS.recv(topic="mission-events", timeout_seconds=3600)
    return {"result": result, "event": event}
```

Retries are opt-in and retry exceptions; `should_retry` can filter transient exceptions. `max_attempts` includes the first attempt. Business declines should be returned as structured outcomes, not raised as retryable errors. Steps cannot start or enqueue workflows; start child workflows from workflow code. Calling a decorated step outside a workflow runs an ordinary function without checkpointing/retries. Official workflow decorator has no `saga` parameter. [Decorator reference](https://docs.dbos.dev/python/reference/decorators), [Steps tutorial](https://docs.dbos.dev/python/tutorials/step-tutorial)

## Durable event loop and launch identity

`DBOS.start_workflow(func, *args)` returns a handle once the workflow is durably created. Use `with SetWorkflowID(mission_id):` around this call to deduplicate repeated start requests. The returned handle has `workflow_id`; existing executions can be retrieved by `DBOS.retrieve_workflow(workflow_id)`. [Methods reference](https://docs.dbos.dev/python/reference/contexts)

```python
from dbos import SetWorkflowID

@app.post("/missions/{mission_id}/start")
def start(mission_id: str):
    with SetWorkflowID(mission_id):
        handle = DBOS.start_workflow(mission, mission_id)
    return {"workflow_id": handle.workflow_id}

@app.post("/missions/{mission_id}/events/{event_id}")
def deliver(mission_id: str, event_id: str, payload: dict):
    DBOS.send(mission_id, payload, topic="mission-events",
              idempotency_key=event_id)
    return {"accepted": True}
```

Messages persist; `recv` consumes FIFO by topic and returns `None` on timeout. A successful send guarantees availability for receiving. Sends from workflow code are exactly once; sends from webhook/normal Python code need `idempotency_key`, scoped to destination workflow. Persisted `DBOS.set_event(key,value)` and `DBOS.get_event(workflow_id,key)` support a status panel, while streams support an ordered activity feed. [Communication tutorial](https://docs.dbos.dev/python/tutorials/workflow-communication)

Use synchronous `def` endpoints with synchronous methods. Inside `async def`, use the corresponding `send_async`, `recv_async`, `start_workflow_async` and other async APIs; do not block the running event loop with synchronous DBOS calls. [AI-assisted development guidance](https://docs.dbos.dev/python/prompting)

## Recovery and financial correctness

Workflow code replays from the beginning using recorded step outcomes, so it must choose the same steps in the same order given recorded results. Put Stripe/Cognee requests, database reads, time, random generation and nondeterministic planner inputs in steps. A pure GOAP search over a recorded state snapshot is safe in orchestration code if tie-breaking is deterministic. Pending interrupted workflows resume on restart using the same persistent database. Completed checkpoints are not executed again. Uncaught workflow exceptions produce `ERROR` and are not automatically recovered as transient failures. [Workflows](https://docs.dbos.dev/python/tutorials/workflow-tutorial)

External I/O is **at least once**: a process can crash after Stripe accepted a request but before its step checkpoint commits. The step then runs again. Use a stable Stripe idempotency key derived from a recorded operation ID and unchanged parameters; compensation gets its own stable key. DBOS workflow identity deduplicates starts but cannot make an external API/database pair atomic. On uncertain payment outcome, reconcile the existing operation before planning another payment. These are application implications of the documented recovery mechanism. [Architecture and recovery](https://docs.dbos.dev/architecture)

For local ledger writes, the current recommended datasource APIs are `SQLAlchemyDatasource.create(url)` and `ds.run_tx_step({"name":"reserve_budget"}, fn, ...)`; `fn` gets its transaction session with `ds.sql_session()`. Results and application mutations commit atomically in that datasource database, providing exactly-once replay for those transactions. Keep external Stripe I/O outside the database transaction. [Transactions and datasources](https://docs.dbos.dev/python/tutorials/transaction-tutorial)

## Saga compensation pattern

Use explicit failure branches or `try/except` in a workflow to invoke compensating steps in reverse order. The official widget-store example waits on a payment message, then explicitly calls `undo_reserve_inventory` and marks the order cancelled on failure. There is no documented automatic compensation decorator/API in the current reference. A search result claiming `@DBOS.workflow(saga=True)` belongs to a different GitHub namespace; do not use it as an official SDK contract. [Official example](https://docs.dbos.dev/python/examples/widget-store)

Recommended application pattern (design inference): persist operation IDs, forward outcomes and required compensation state; execute each compensating external action as an idempotent DBOS step. Distinguish cancelled authorization, refunded charge, reversed internal transfer and unrecallable posted payout. If compensation is exhausted, record `compensation_required` and keep an explicit retry workflow/queue; an uncaught exhausted compensation exception must not become a falsely successful mission. Add a deterministic crash/restart test around Stripe success before checkpoint, around a delivery event wait, and during compensation.

## Proposed delivery simulation

Start one mission workflow with a stable ID. After planning and authorised payment, call `DBOS.recv("mission-events", timeout_seconds=...)`. A separate demo event producer or manual UI posts `delayed`, `failed`, or `delivered` with unique event IDs. The workflow records the received event, obtains a fresh policy/state snapshot in a step, runs deterministic GOAP, then performs eligible compensation and replacement payment. Repeatedly interrupting and restarting the service while waiting provides a credible durable-execution demonstration. Label delivery events as simulation and Stripe operations as Stripe test mode.
