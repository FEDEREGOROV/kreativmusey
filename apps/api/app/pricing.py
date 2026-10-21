from __future__ import annotations

from dataclasses import dataclass

from .models import ApiCall


TOKENS_PER_MILLION = 1_000_000


@dataclass(frozen=True)
class TokenRates:
    input_per_million: float
    output_per_million: float = 0.0
    cached_input_per_million: float | None = None


# OpenAI Standard, short-context prices per 1M tokens, verified 2026-10-21.
# The chat context is capped far below the long-context threshold.
MODEL_RATES = {
    "gpt-6-luna": TokenRates(
        input_per_million=0.10,
        output_per_million=0.50,
        cached_input_per_million=0.01,
    ),
    "text-embedding-3-small": TokenRates(input_per_million=0.02),
}


def rates_for_model(model: str) -> TokenRates | None:
    if model in MODEL_RATES:
        return MODEL_RATES[model]
    for base_model, rates in MODEL_RATES.items():
        if model.startswith(f"{base_model}-"):
            return rates
    return None


def calculate_token_cost(
    model: str,
    input_tokens: int | None,
    output_tokens: int | None = None,
    cached_tokens: int | None = None,
) -> float | None:
    rates = rates_for_model(model)
    if rates is None:
        return None

    input_count = max(0, input_tokens or 0)
    output_count = max(0, output_tokens or 0)
    cached_count = min(input_count, max(0, cached_tokens or 0))
    uncached_count = input_count - cached_count
    cached_rate = rates.cached_input_per_million
    if cached_rate is None:
        cached_rate = rates.input_per_million

    cost = (
        uncached_count * rates.input_per_million
        + cached_count * cached_rate
        + output_count * rates.output_per_million
    ) / TOKENS_PER_MILLION
    return round(cost, 12)


def price_api_call(call: ApiCall) -> ApiCall:
    cost = calculate_token_cost(
        call.model,
        call.input_tokens,
        call.output_tokens,
        call.cached_tokens,
    )
    if cost is not None:
        call.cost_usd = cost
        call.cost_status = "known"
    return call


def resolved_call_cost(call: ApiCall) -> float | None:
    if call.cost_status == "known" and call.cost_usd is not None:
        return call.cost_usd
    return calculate_token_cost(
        call.model,
        call.input_tokens,
        call.output_tokens,
        call.cached_tokens,
    )
