"""Per-token pricing used to estimate the cost line in the build note.

Confirmed 2026-09-28 against OpenRouter's live listings (same underlying
models, Anthropic-direct pricing matches): openrouter.ai/anthropic/claude-haiku-4.5,
openrouter.ai/anthropic/claude-sonnet-5, openrouter.ai/anthropic/claude-opus-5.5.
Re-check before quoting the final cost line to the client — prices change.
"""

# USD per 1M tokens, (input, output).
RATES_PER_MILLION_TOKENS = {
    # Anthropic-direct model ids
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    # OpenRouter model slugs (same underlying models) — see src/settings.py's
    # ALLOWED_MODELS for the exact set the UI's model selector offers.
    "anthropic/claude-haiku-4.5": (1.00, 5.00),
    "anthropic/claude-sonnet-5": (2.00, 10.00),
    "anthropic/claude-opus-5.5": (4.00, 20.00),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    input_rate, output_rate = RATES_PER_MILLION_TOKENS.get(model, (0.0, 0.0))
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
