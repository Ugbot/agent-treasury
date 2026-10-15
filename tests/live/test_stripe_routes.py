"""Live sandbox probe. Skips without STRIPE_SECRET_KEY (sk_test_/rk_test_)."""
import os

import pytest

from treasury.probe import run_probe

KEY = os.environ.get("STRIPE_SECRET_KEY", "")
pytestmark = pytest.mark.skipif(not KEY.startswith(("sk_test_", "rk_test_")),
                                reason="STRIPE_SECRET_KEY sandbox key not set")


def test_sandbox_routes():
    rep = run_probe()
    assert rep["livemode"] is False
    for name, cap in rep["capabilities"].items():
        assert cap["status"] in {"ok", "unavailable", "partial", "failed"}, name
        if cap["status"] == "unavailable":
            assert cap["setup"], name
    assert rep["capabilities"]["payments"]["status"] == "ok"
