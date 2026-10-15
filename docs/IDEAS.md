# Current product direction

The core is payments based on written policy and changing inputs. The user selected basic GOAP, Cognee policy memory and DBOS durable execution/sagas, with no dependency on the earlier repository.

Use an Uber-style delivery simulator to demonstrate merchant bills, capped fuel-card buying, customer refunds and driver payouts. Policy checks cover supplied insurance records, travel zones, fuel entitlements, approval thresholds, funding and recipient receipt. A website provides triggers and graphics showing what happens. Actual AI in a sidebar explains decisions and drafts local messages.

Policies can be added and chained while running, and attached to individual payments, jobs, cards and counterparties. An example chain issues an invoice above an amount threshold, waits for actual payment, requires coverage and receipt, then permits disbursement. The map and AI never supply financial authority.

The user approved the delivery direction; the latest [build plan](PLAN.md), [policy model](POLICY_MODEL.md), [frontend plan](FRONTEND_PLAN.md) and [backlog](BACKLOG.md) are the implementation references. Earlier launch-day/event procurement ideas were exploratory and are not the selected scope.
