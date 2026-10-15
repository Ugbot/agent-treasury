# Agent Treasury build plan

15 October 2026. The user approved the delivery scenarios and selected the stack below. This plan incorporates the later requirements for actual AI, adding/chaining policies at runtime, attaching policies to payments, and explicit frontend/backend tickets.

## The core

**Inputs -> attached/chained written policies -> deterministic decision -> payment -> evidence.**

Delivery is the visual example. Insurance, travel, fuel cards, invoices, receipt confirmation and approvals are inputs and rules around the same payment mechanism. The map is an input simulator; graphics explain state. Chat supports understanding and drafting. Neither the UI, GOAP nor a language model may override financial policy.

## Selected stack

- Python 3.13 and FastAPI for the local API.
- A small, application-owned GOAP planner with deterministic search and explicit preconditions/effects.
- Cognee Cloud/REST for versioned policy documents, clause/evidence relationships and decision memory. A validated local immutable snapshot supplies deterministic evaluation.
- DBOS Python for durable workflows, event waits, steps and explicit compensating actions. SQLite for a single-server demo, with separate persistent system/application databases.
- Stripe test Payments, Invoicing, Connect Transfers and Issuing.
- React + TypeScript + Vite for trigger controls and graphics. Use an SVG map in simulator metre coordinates; no routing API or external map tiles are required.
- A configured real model API for read-only chat/explanations and local message drafts. Use the configured real-model adapter, with provider/model shown and account access verified; the concurrent implementation currently supplies an Anthropic adapter. No provider-specific model is a product requirement.

No TaskEngine, LittleHorse, Kafka, Redis or dependency on the old repository.

## Documents

- [Architecture and execution](ARCHITECTURE.md)
- [Policy attachments and chains](POLICY_MODEL.md)
- [API and event contract](API_CONTRACT.md)
- [Frontend triggers and graphics](FRONTEND_PLAN.md)
- [Scenarios and recording](SCENARIOS.md)
- [Task-by-task implementation plan](superpowers/plans/2026-10-15-agent-treasury.md)
- [Tracker backlog and dependency map](BACKLOG.md)
- [Stripe capabilities](API_ROUTES.md)
- [DBOS research](research/2026-10-15-dbos-execution.md)

## Build order

1. Prove the durable policy/input/payment loop and the actual Stripe sandbox routes independently.
2. Attach policies from Cognee; then add the runtime chain editor and deterministic obligation evaluator.
3. Build frontend trigger controls from the frozen contract while the backend develops invoice, travel, settlement, fuel and refund behaviours.
4. Add real AI explanations/drafts in parallel with payment scenarios after policy memory exists.
5. Prove saga recovery and integrate the graphics with actual provider state.
6. Rehearse, publish the OSS repo and record the two-minute submission.

The tracker is project ATREAS, epic ATREAS-1. Every implementation ticket stays open until its acceptance evidence exists; planned test names in these documents are not passing test claims.

## Delivery boundaries

Must demonstrate actual Stripe test money movement, written/attached/chained policy, evidence-based explanations, GOAP replanning, DBOS recovery/compensation, four payment examples, and actual model-generated text. A missing product/key is displayed as unavailable, never silently replaced by fake success. Public GitHub publication, accessible video and submission remain work to do.

Working product assumption: sender is the merchant, recipient/customer pays for goods plus delivery. The alternative shipping-deposit interpretation changes the financial split, not the policy engine.

## Current readiness

This document records the requested plan. Implementation started concurrently in another workstream while it was being written; consult current source and actual test evidence rather than treating this plan as a completion report. The process environment was checked for named Stripe, Cognee and model credentials without displaying values; none were configured there. Keys may be supplied later through ignored local configuration. No authenticated provider call has been made, and account capabilities remain unverified.
