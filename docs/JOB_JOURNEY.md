# Customer job journey (ATREAS-24..32)

Define a delivery job, attach policies from a library, preview, dispatch once, watch it happen.

## Backend
`src/treasury/jobs.py` (`JobService`, `JobIn`, templates) and `routes_jobs.py` (`router`).

| Route | Purpose |
|---|---|
| `POST /jobs` | Typed, validated job (pickup, dropoff, goods value, price, delivery fee, deadline, insurance_required). Integer minor units. Currency defaults from CURRENCY-1 of the effective org policy and is rejected if not permitted. Job id is a stable hash of the content (same job twice returns the same id). |
| `GET /jobs/{id}` | Job, frozen policy versions after dispatch. |
| `GET /jobs/policy-library` (also `GET /policies/library`) | Registry policies plus seeded templates: Fuel and meal allowance, Insurance required, Approve above a limit (parameter), Refund before pickup. Mandatory org rules are `locked`. |
| `GET/POST /jobs/{id}/policies`, `DELETE /jobs/{id}/policies/{att}` | Effective rules; attach with `expected_version` (409 on stale). Org rules cannot be attached/detached; insurance cannot be detached when the job requires it. |
| `POST /jobs/{id}/preview` | Dry run over named situations (receipt missing, prohibited zone, over limit, insurance lapsed, happy path). Pure `evaluate()` over the effective attachments: no audit rows, no registry decisions, no mission, no payment. |
| `POST /jobs/{id}/dispatch` | Idempotent. Freezes attached versions (locks the `job` target), creates the mission (`job_id` input, so the DecisionEngine inherits the job's policies), starts the durable workflow once, audits with party `sender`. If the workflow runtime is not configured the result says `workflow.started=false, unavailable=true`; nothing is faked. |
| `GET /jobs/{id}/watch?after=seq` | Plain-English narrated steps from the audit log, `status` (draft, dispatched, held, approved, denied, paid), `done`, per-step `mode` and `simulated`. |

Audit kinds written: `job.created`, `job.policy_attached`, `job.policy_detached`, `job.dispatched`, `job.workflow_started` / `job.workflow_unavailable`, `job.inputs`. All carry `mission_id` (`mission-<job_id>`, fixed at creation) and `party=sender`.

## Integrator hooks
- Mount `routes_jobs.router` at root and under `/api`. Include it **before** `routes_policies.router`, otherwise `GET /policies/library` is shadowed by `/policies/{policy_id}` (the frontend uses `/jobs/policy-library`, which is never shadowed).
- `app.state.ledger` is required. `app.state.jobs` (a prebuilt `JobService`) and `app.state.gateway_mode` (callable returning `mock` / `stripe_test`) are optional; the registry comes from `routes_policies.get_registry()`.
- For the workflow to see job policies the app's `DecisionEngine` must use the same registry (already true in `create_app`).

## Frontend
`frontend/src/journey/`: `JourneyView` (4-step wizard, effective-rules panel, preview table, live watch polling `/api/jobs/{id}/watch` every second). Props: `client`, `pollMs`, `onExit`. Without `VITE_JOURNEY_API` it uses the fixture client; set `VITE_JOURNEY_API=live` for `/api`. The integrator adds the "New job" entry point in `App.tsx`.
