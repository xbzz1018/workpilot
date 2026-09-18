from __future__ import annotations

from types import SimpleNamespace

from langchain.agents.middleware.types import ModelResponse
from langchain.agents.middleware.types import ExtendedModelResponse
from langchain_core.messages import AIMessage

from workpilot.audit import ModelCallBudget, ModelCallBudgetExceeded, TokenAuditMiddleware, UsageCollector
from workpilot.pricing import PriceBook


def test_price_null_propagates_for_missing_usage_or_price() -> None:
    book = PriceBook.load_default()
    assert book.estimate("deepseek-chat", 100, 20, 0) is None
    assert book.estimate("deepseek-v4-flash:off_peak", 100, 20, None) is None


def test_known_price_formula() -> None:
    book = PriceBook.load_default()
    cost = book.estimate("deepseek-v4-flash:off_peak", 1_000_000, 1_000_000, 500_000)
    assert cost == 0.7735


def test_audit_records_usage_and_cache() -> None:
    collector = UsageCollector()
    middleware = TokenAuditMiddleware("main", collector)
    request = SimpleNamespace(model=SimpleNamespace(model_name="deepseek-chat"))
    response = ModelResponse(result=[AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 14,
            "input_token_details": {"cache_read": 3},
        },
    )])
    assert middleware.wrap_model_call(request, lambda _: response) is response
    record = collector.records[0]
    assert record.cache_read_tokens == 3
    assert record.estimated_cost_usd is None


def test_audit_handles_extended_response_metadata_fallback() -> None:
    collector = UsageCollector()
    middleware = TokenAuditMiddleware("baseline", collector)
    request = SimpleNamespace(model=SimpleNamespace(model="deepseek-chat"))
    message = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        response_metadata={"token_usage": {"prompt_cache_hit_tokens": 4}},
    )
    response = ExtendedModelResponse(model_response=ModelResponse(result=[message]))
    middleware.wrap_model_call(request, lambda _: response)
    assert collector.records[0].cache_read_tokens == 4


def test_model_call_budget_is_enforced_before_handler() -> None:
    budget = ModelCallBudget(1)
    collector = UsageCollector()
    middleware = TokenAuditMiddleware("main", collector, budget=budget)
    request = SimpleNamespace(model=SimpleNamespace(model="m"))
    response = AIMessage(content="ok")
    assert middleware.wrap_model_call(request, lambda _: response) is response
    with __import__("pytest").raises(ModelCallBudgetExceeded):
        middleware.wrap_model_call(request, lambda _: AIMessage(content="should-not-run"))
    assert budget.used == 1
