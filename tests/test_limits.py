import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from workpilot.limits import RoleConcurrencyMiddleware


def test_role_concurrency_is_bounded_to_two() -> None:
    middleware = RoleConcurrencyMiddleware("test-role", max_concurrency=2)
    active = 0
    peak = 0
    guard = threading.Lock()

    def handler(_):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with guard:
            active -= 1
        return "ok"

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: middleware.wrap_model_call(None, handler), range(3)))
    assert results == ["ok", "ok", "ok"]
    assert peak == 2


def test_role_concurrency_rejects_zero() -> None:
    with pytest.raises(ValueError, match="positive"):
        RoleConcurrencyMiddleware("bad", max_concurrency=0)
