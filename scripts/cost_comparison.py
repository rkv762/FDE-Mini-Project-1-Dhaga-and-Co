#!/usr/bin/env python3
"""Compares the cost of three model-assignment scenarios for one pipeline run,
to justify the two-model split in docs/build-note.md with numbers, not just
narrative.

Token counts here are ESTIMATES (prompt sizes in src/chains.py times a rough
tokens-per-word ratio), not measurements — clearly labelled as such. Replace
with real figures from data/decision_log.jsonl once the group has run this
against the live API (each PipelineResult already carries real usage).

Run: python scripts/cost_comparison.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pricing import estimate_cost_usd  # noqa: E402

# Rough per-call token estimates, one row per model-call step in the pipeline
# (see docs/architecture.md). Based on the prompt templates in src/chains.py
# plus a handful of catalogue/history rows — not measured, estimated.
STEPS = [
    # step name,        input_tokens, output_tokens
    ("extract_profile",       450, 150),
    ("route_persona",         250,  60),
    ("persona_safety_check",  300,  60),
    ("shortlist_skus",        500, 120),
    ("draft_message",         350, 120),
    ("evaluate_message",      400,  90),
]

MODEL_A = "anthropic/claude-haiku-4.5"
MODEL_B = "anthropic/claude-sonnet-5"

# Steps in the actual pipeline: extract/route/safety/shortlist on Model A,
# draft/evaluate on Model B (see docs/architecture.md's code-vs-model table).
ACTUAL_ASSIGNMENT = {
    "extract_profile": MODEL_A,
    "route_persona": MODEL_A,
    "persona_safety_check": MODEL_A,
    "shortlist_skus": MODEL_A,
    "draft_message": MODEL_B,
    "evaluate_message": MODEL_B,
}


def run_cost(assignment: dict) -> float:
    return sum(
        estimate_cost_usd(assignment[step], input_tokens, output_tokens)
        for step, input_tokens, output_tokens in STEPS
    )


def main():
    all_a = {step: MODEL_A for step, _, _ in STEPS}
    all_b = {step: MODEL_B for step, _, _ in STEPS}

    scenarios = {
        "All calls on Model A (Haiku 4.5 only)": run_cost(all_a),
        "All calls on Model B (Sonnet 5 only)": run_cost(all_b),
        "Our split (A for extract/route/safety/shortlist, B for draft/evaluate)": run_cost(ACTUAL_ASSIGNMENT),
    }

    print("Estimated cost per single pipeline run (one customer, one approved draft, no retries):\n")
    for label, cost in scenarios.items():
        print(f"  {label}: ${cost:.5f}")

    weekly_batch = 5000  # dormant/repeat-window customers targeted per week — an assumption, not all 48k orders/week
    print(f"\nExtrapolated to a weekly re-engagement batch of {weekly_batch:,} customers (assumption, not all 48,000 orders/week):\n")
    for label, cost in scenarios.items():
        print(f"  {label}: ${cost * weekly_batch:,.2f}/week")


if __name__ == "__main__":
    main()
