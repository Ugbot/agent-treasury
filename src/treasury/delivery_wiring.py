"""Point every route module's service at the selected gateway.

Integrator hook: ``configure_services(gw, db=led.db, audit=AuditLog(led.db))`` so fuel counters
persist in the ledger database and every effect lands in the audit log. Called with only a
gateway, services keep working (fuel counters persist to TREASURY_DB when that env var is set,
otherwise memory) and the gateway/audit pairing is left as is.
"""
from __future__ import annotations

import os
import sqlite3

from . import routes_delivery, routes_fuel, routes_refunds
from .fuel import FuelService
from .proof import SettlementService
from .refunds import RefundService


def configure_services(gateway, db: sqlite3.Connection | None = None, audit=None) -> None:
    if db is None and os.environ.get("TREASURY_DB"):
        db = sqlite3.connect(os.environ["TREASURY_DB"], check_same_thread=False)
        db.row_factory = sqlite3.Row
    if audit is None and db is not None:
        from .audit import AuditLog
        audit = AuditLog(db)
    if audit is not None and hasattr(gateway, "set_audit") and getattr(gateway, "audit", None) is None:
        gateway.set_audit(audit)
    routes_delivery.set_service(SettlementService(gateway, audit=audit))
    routes_fuel.set_service(FuelService(gateway, db=db, audit=audit))
    routes_refunds.set_service(RefundService(gateway, audit=audit))
