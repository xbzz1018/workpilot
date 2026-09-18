"""Versioned token pricing with explicit null propagation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files


@dataclass(frozen=True)
class PriceBook:
    version: str
    models: dict[str, dict[str, float | str | None]]

    @classmethod
    def load_default(cls) -> PriceBook:
        path = files("workpilot").joinpath("config/pricing.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(version=data["version"], models=data["models"])

    def estimate(
        self,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        cache_read_tokens: int | None,
    ) -> float | None:
        prices = self.models.get(model)
        if prices is None or input_tokens is None or output_tokens is None or cache_read_tokens is None:
            return None
        hit_price = prices.get("input_cache_hit_per_million_usd")
        miss_price = prices.get("input_cache_miss_per_million_usd")
        output_price = prices.get("output_per_million_usd")
        if not all(isinstance(value, (int, float)) for value in (hit_price, miss_price, output_price)):
            return None
        input_miss = max(input_tokens - cache_read_tokens, 0)
        return round(
            (
                cache_read_tokens * float(hit_price)
                + input_miss * float(miss_price)
                + output_tokens * float(output_price)
            )
            / 1_000_000,
            12,
        )

