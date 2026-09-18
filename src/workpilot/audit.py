"""Per-call model usage and latency collection for main and subagents."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from workpilot.pricing import PriceBook
from workpilot.schemas import UsageRecord


class UsageCollector:
    def __init__(self) -> None:
        self._records: list[UsageRecord] = []
        self._lock = threading.Lock()

    def append(self, record: UsageRecord) -> None:
        with self._lock:
            self._records.append(record)

    @property
    def records(self) -> list[UsageRecord]:
        with self._lock:
            return list(self._records)

    def mark_since(self, index: int, retry_count: int) -> None:
        with self._lock:
            for position in range(index, len(self._records)):
                self._records[position] = self._records[position].model_copy(update={"retry_count": retry_count})


class ModelCallBudgetExceeded(RuntimeError):
    pass


class ModelCallBudget:
    """Thread-safe application-level model invocation budget."""

    def __init__(self, limit: int, used: int = 0) -> None:
        if limit < 1 or used < 0 or used > limit:
            raise ValueError("invalid model call budget")
        self.limit = limit
        self._used = used
        self._lock = threading.Lock()

    def reserve(self) -> int:
        with self._lock:
            if self._used >= self.limit:
                raise ModelCallBudgetExceeded(f"model call budget exhausted ({self._used}/{self.limit})")
            self._used += 1
            return self._used

    @property
    def used(self) -> int:
        with self._lock:
            return self._used


class TokenAuditMiddleware(AgentMiddleware):
    """Observe one agent's model calls without mutating its behavior."""

    def __init__(self, agent_name: str, collector: UsageCollector, price_book: PriceBook | None = None, *, requested_model: str | None = None, key_alias: str | None = None, budget: ModelCallBudget | None = None) -> None:
        self.agent_name = agent_name
        self.collector = collector
        self.price_book = price_book or PriceBook.load_default()
        self.requested_model = requested_model
        self.key_alias = key_alias
        self.budget = budget

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        if self.budget is not None:
            self.budget.reserve()
        started = time.perf_counter()
        try:
            response = handler(request)
        except Exception as exc:
            model_name = getattr(request.model, "model_name", None) or getattr(request.model, "model", None) or "unknown"
            self.collector.append(UsageRecord(
                call_id=uuid.uuid4().hex,
                agent_name=self.agent_name,
                model=model_name,
                duration_ms=(time.perf_counter() - started) * 1000,
                requested_model=self.requested_model or model_name,
                actual_model=None,
                key_alias=self.key_alias,
                error_type=type(exc).__name__,
            ))
            raise
        duration_ms = (time.perf_counter() - started) * 1000
        messages = self._messages(response)
        ai_message = next((message for message in reversed(messages) if isinstance(message, AIMessage)), None)
        usage = (getattr(ai_message, "usage_metadata", None) or {}) if ai_message else {}
        details = usage.get("input_token_details") or {}
        cache_read = details.get("cache_read")
        if cache_read is None and ai_message is not None:
            token_usage = (getattr(ai_message, "response_metadata", None) or {}).get("token_usage") or {}
            cache_read = token_usage.get("prompt_cache_hit_tokens")
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        total_tokens = usage.get("total_tokens")
        requested_model = getattr(request.model, "model_name", None) or getattr(request.model, "model", None) or "unknown"
        response_metadata = (getattr(ai_message, "response_metadata", None) or {}) if ai_message else {}
        actual_model = response_metadata.get("model_name") or response_metadata.get("model") or requested_model
        cost = self.price_book.estimate(actual_model, input_tokens, output_tokens, cache_read)
        self.collector.append(UsageRecord(
            call_id=uuid.uuid4().hex,
            agent_name=self.agent_name,
            model=actual_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cache_read_tokens=cache_read,
            duration_ms=duration_ms,
            pricing_version=self.price_book.version,
            estimated_cost_usd=cost,
            requested_model=self.requested_model or requested_model,
            actual_model=actual_model,
            key_alias=self.key_alias,
        ))
        return response

    @staticmethod
    def _messages(response: Any) -> list[Any]:
        if isinstance(response, AIMessage):
            return [response]
        if hasattr(response, "model_response"):
            response = response.model_response
        return list(getattr(response, "result", []) or [])
