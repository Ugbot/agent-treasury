# Agent Treasury

A hackathon project exploring delivery payments governed by travel policy, using basic GOAP planning, Cognee policy memory and DBOS execution/sagas: merchant bills, capped fuel-card buying, customer refunds and driver payouts.

**Status: research and design. There is no runnable application yet.**

The goal is to execute Stripe test-mode operations under a shared written policy, replan unfinished work when constraints change, and explain every payment using the policy version and evidence that authorised it. Cognee supplies shared policy and decision memory.

## Working documents

- [Build plan and ticket index](docs/PLAN.md)
- [Backend/frontend backlog](docs/BACKLOG.md)
- [Policy chaining and attachments](docs/POLICY_MODEL.md)
- [Frontend triggers and graphics](docs/FRONTEND_PLAN.md)

- [Delivery map and policy scenarios](docs/DELIVERY_DEMO.md)
- [Combined product idea](docs/IDEAS.md)
- [API routes and practical boundaries](docs/API_ROUTES.md)
- [Detailed Issuing, Treasury, SPT and MPP research](docs/research/2026-10-15-stripe-specialist-routes.md)

## Hackathon requirements

The [Briefcase × Stripe brief](https://hackathons.briefcase.so/stripe-october-2026/) requires Stripe test mode, a written spending policy, evidence-based explanations, a public GitHub repository and a demo video of at most two minutes. Project implementation starts during the permitted build window; planning and documentation may precede it.

This is a fresh standalone repository. The selected runtime is DBOS, with an application-owned GOAP planner; Cognee stores policy/evidence history. Policies can be added, chained and attached to individual payments and entitlements. The frontend generates simulation inputs and visualises backend decisions; actual AI provides explanations and local message drafts. Account-specific Stripe capabilities remain unverified.

## Licence

New project material is available under the [MIT licence](LICENSE). Dependencies retain their own licences. Publishing the public GitHub repository is still a submission task.
