# Agent Treasury

A hackathon project exploring delivery payments governed by travel policy, coordinated by TaskEngine: merchant bills, capped fuel-card buying, customer refunds and driver payouts.

**Status: research and design. There is no runnable application yet.**

The goal is to execute Stripe test-mode operations under a shared written policy, replan unfinished work when constraints change, and explain every payment using the policy version and evidence that authorised it. Cognee supplies shared policy and decision memory.

## Working documents

- [Delivery map and policy scenarios](docs/DELIVERY_DEMO.md)
- [Combined product idea](docs/IDEAS.md)
- [API routes and practical boundaries](docs/API_ROUTES.md)
- [Detailed Issuing, Treasury, SPT and MPP research](docs/research/2026-10-15-stripe-specialist-routes.md)

## Hackathon requirements

The [Briefcase × Stripe brief](https://hackathons.briefcase.so/stripe-october-2026/) requires Stripe test mode, a written spending policy, evidence-based explanations, a public GitHub repository and a demo video of at most two minutes. Project implementation starts during the permitted build window; planning and documentation may precede it.

This is a fresh repository. TaskEngine is an intended dependency from [AI-by-Wire](https://github.com/Ugbot/aibywire); no implementation code has been copied here. Account-specific Stripe capabilities remain unverified.

## Licence

New project material is available under the [MIT licence](LICENSE). Dependencies retain their own licences. Publishing the public GitHub repository is still a submission task.
