"""Gateway selection and adapters between module interfaces."""
from __future__ import annotations

import os

from . import gateway as gw
from . import saga


def gateway_from_env() -> gw.PaymentGateway:
    """MockGateway by default; StripeGateway (test mode only) when STRIPE_SECRET_KEY is set.
    A live key raises LiveModeError rather than silently falling back."""
    if os.environ.get("STRIPE_SECRET_KEY"):
        from .stripe_gateway import StripeGateway
        return StripeGateway()
    return gw.MockGateway()


class SagaGatewayAdapter:
    """Presents a gateway.PaymentGateway as the saga store's pay/reconcile/refund protocol."""

    def __init__(self, inner: gw.PaymentGateway):
        self.inner = inner
        self.mode = inner.mode  # type: ignore[assignment]

    def _wrap(self, fn):
        try:
            return fn()
        except gw.CapabilityUnavailable as e:
            raise saga.GatewayUnavailable(f"{e}. Setup: {e.setup}") from e
        except gw.LiveModeError:
            raise
        except gw.GatewayError as e:
            raise saga.ProviderError(str(e)) from e

    def pay(self, operation_id, amount_minor, currency, idempotency_key):
        r = self._wrap(lambda: self.inner.create_payment(idempotency_key, amount_minor, currency.lower()))
        if r.status != "succeeded":
            raise saga.ProviderError(f"payment not succeeded: {r.status}")
        return saga.ProviderResult(r.provider_ref, r.mode)

    def reconcile(self, operation_id):
        # Provider-side lookup is not part of the gateway contract; retries reuse the same
        # idempotency key, so the provider returns the original object instead of a new one.
        return None

    def refund(self, operation_id, provider_ref, idempotency_key):
        r = self._wrap(lambda: self.inner.refund(idempotency_key, provider_ref))
        return saga.ProviderResult(r.provider_ref, r.mode)
