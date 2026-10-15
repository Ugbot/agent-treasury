# Frontend: triggers and graphics

## The job of the frontend

Give the operator a series of visible input triggers and graphics that explain what happens next. The backend owns policy verdicts and financial state. A button represents a command, never a promise that payment succeeded.

## Screen

A compact header names the current mission, simulation/test mode and provider readiness. The centre is a city map and policy/payment flow. A left trigger deck controls inputs; a right sidebar switches between evidence and real AI chat. A lower event/saga timeline links every transition. Use a deliberate operations-console visual style, strong money typography, restrained colour and clear legends. Keep loading, hold, denial and partial/unknown states as legible as success.

| Trigger/control | Input/command | Graphic feedback |
|---|---|---|
| New scenario | Mission create with isolated identity | Initial map, obligations and ledger; previous provider history retained |
| Adjust amount | Versioned amount proposal | Threshold crossing, invoice/approval branch lights up |
| Add/connect policy | Typed graph edit and preview | Nodes/edges, all-of joins, failed clauses and validation |
| Attach policy to payment | Versioned attachment | Policy badges, inherited mandatory gates, effective hash/re-evaluation |
| Change coverage | Supplied demo coverage event | Driver eligibility and route/coverage layers |
| Road/route change | Route event | Original route fades; permitted replacement and plan lineage appear |
| Move/step driver | Position/sim-time event | Driver marker, destination radius, fuel corridor and evidence age |
| Fuel attempt | Amount/category/merchant input | Entitlement meter, allowed/declined authorization and capture states |
| Confirm receipt | One-use recipient confirmation | Gate checklist; money release only after backend confirmation |
| Approve pending amount | Bound approval command | Approval gate passes for the unchanged operation |
| Cancel before pickup | Cancellation event | Pending plan stops; refund route/status appears |
| Ask why / draft message | Read-only chat/draft request | Cited explanation or editable local draft |
| Replay/scrub | Read-only historical view | Prior route/rules/decisions, with no new commands |

## Frontend tickets

ATREAS-14 owns the Vite/React shell, shared types, API client and trigger/event presentation. ATREAS-15 owns policy graph/editor/attachment graphics. ATREAS-16 owns map movement, overlays and travel inputs. ATREAS-17 owns money, card/invoice/refund/saga and evidence graphics. ATREAS-18 owns chat and message drafts. ATREAS-19 integrates actual backend state, tests full scenarios and builds the recording director.

Components live in `frontend/src/components/TriggerDeck.tsx`, `PolicyGraph.tsx`, `PolicyAttachments.tsx`, `DeliveryMap.tsx`, `MoneyFlow.tsx`, `EvidencePanel.tsx`, `SagaTimeline.tsx`, `ChatPanel.tsx` and `ScenarioDirector.tsx`. Shared contract/client code is `frontend/src/api.ts` and `types.ts`. Feature tickets own their component files; shell/client changes coordinate through ATREAS-14. Backend tickets own server behaviour; frontend counterparts visualise their accepted states.

## Interaction rules

Render accepted snapshots by monotonically increasing revision. Disable or show pending while mutations run, handle 409 by refreshing/previewing again, and never optimistically paint money as confirmed. Distinguish mock, fixture and Stripe-test receipts. Show stale location/policy age. Use accessible labels and text alternatives for every graph colour, keyboard controls, reduced-motion support and responsive layouts. Escape all model/policy/invoice content.

Use contract fixtures only for isolated component development and make fixture mode explicit. The integrated demo must prove that changing the triggers changes the backend decision and actual test operation. Core screenshots should capture one policy hold, one replanned route, one confirmed payment and one why-paid inspection.
