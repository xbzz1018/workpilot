"""Process-wide role/model concurrency bounds for the single-node worker."""

from __future__ import annotations

import threading
from typing import Any

from langchain.agents.middleware import AgentMiddleware


class RoleConcurrencyMiddleware(AgentMiddleware):
    _locks: dict[str, threading.BoundedSemaphore] = {}
    _guard = threading.Lock()

    def __init__(self, limit_key: str, max_concurrency: int = 2) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self.limit_key = limit_key
        with self._guard:
            self._locks.setdefault(limit_key, threading.BoundedSemaphore(max_concurrency))

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        with self._locks[self.limit_key]:
            return handler(request)
