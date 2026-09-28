"""Tunable pipeline settings, exposed via the UI's Settings panel and
GET /api/settings.

Deliberately stateless server-side: Vercel's deployed functions don't share
memory reliably across requests (see docs/build-note.md — we already got
burned once assuming local-process state survives on Vercel, with the
decision log). Instead the frontend holds the current settings and sends
them on every /api/recommend call; the server just validates and applies
them for that one run. "No restart needed" is true here by construction,
not just in the UI copy.
"""
from typing import Optional

from pydantic import BaseModel, Field

# Exactly three models, deliberately — not an open-ended list, and ordered
# cheapest to most expensive. That order matters: it's also the escalation
# path "Auto" mode climbs (see AUTO_MODEL_B below).
ALLOWED_MODELS = [
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-5",
    "anthropic/claude-opus-5.5",
]

MODEL_LABELS = {
    "anthropic/claude-haiku-4.5": "Haiku 4.5 (cheap, fast)",
    "anthropic/claude-sonnet-5": "Sonnet 5 (balanced, default)",
    "anthropic/claude-opus-5.5": "Opus 5.5 (highest quality, most expensive)",
}

# A sentinel, not a real model id. When Model B is set to this, the pipeline
# stops using one fixed model for the draft/evaluate steps and instead climbs
# ALLOWED_MODELS cheapest-first, escalating one tier only when the evaluator
# says the current tier isn't good enough — see pipeline.py's evaluator loop.
AUTO_MODEL_B = "auto"

AUTO_MODEL_B_HELP = (
    "Instead of a fixed model, try Haiku 4.5 first (cheapest); if the evaluator "
    "doesn't approve it above the threshold, escalate to Sonnet 5, then Opus 5.5 "
    "if needed. Never pays for a stronger model than the message actually required. "
    "This is what keeps the cost line viable at Dhaga & Co.'s volume without capping "
    "quality — cheaper re-engagement means more customers can be reached for the "
    "same budget, which is the whole point of fixing the repeat-purchase problem."
)

DEFAULT_MODEL_A = "anthropic/claude-haiku-4.5"
# Auto by default — the cost-ascending cascade is the actual answer to "pick
# the model automatically whose price is less and output is high viable,"
# not an opt-in buried in settings. See AUTO_MODEL_B_HELP below.
DEFAULT_MODEL_B = AUTO_MODEL_B
DEFAULT_EVALUATOR_THRESHOLD = 0.7
DEFAULT_CRITIC_ENABLED = True
DEFAULT_MAX_REVISION_ROUNDS = 2

THRESHOLD_HELP = (
    "The minimum evaluator score (0-1) a draft needs to ship without a human. "
    "Low (e.g. 0.3): more drafts are auto-approved — faster and cheaper, but a "
    "weaker quality bar before something reaches a real customer. High (e.g. 0.9): "
    "a stricter bar — more drafts get sent back for revision or escalated to "
    "needs_human_review, which is safer but costs more in retries and human time."
)
CRITIC_HELP = (
    "When on (default), every draft is scored by the evaluator before it can ship "
    "— this is what makes the pipeline 'safe to publish unread' per the brief. "
    "When off, the first draft ships as-is with no safety net: faster and cheaper "
    "per run, but nothing catches an off-tone or ungrounded message."
)
REVISION_ROUNDS_HELP = (
    "How many times the evaluator can send a rejected draft back for a rewrite "
    "before the pipeline gives up and escalates to needs_human_review. 0 disables "
    "revision (reject once, escalate immediately, still safe but leans harder on "
    "human review); higher values retry harder before escalating. In Auto mode "
    "this also caps how far up the model tiers a draft is allowed to climb."
)


class PipelineSettings(BaseModel):
    model_a: str = Field(default=DEFAULT_MODEL_A)
    model_b: str = Field(default=DEFAULT_MODEL_B, description=f"A model id, or '{AUTO_MODEL_B}'.")
    evaluator_threshold: float = Field(default=DEFAULT_EVALUATOR_THRESHOLD, ge=0.0, le=1.0)
    critic_enabled: bool = Field(default=DEFAULT_CRITIC_ENABLED)
    max_revision_rounds: int = Field(default=DEFAULT_MAX_REVISION_ROUNDS, ge=0, le=4)


def resolve_model(name: Optional[str], default: str, allow_auto: bool = False) -> str:
    """Falls back to the default rather than erroring on an unknown model —
    a typo'd query param should degrade gracefully, not break the request."""
    if allow_auto and name == AUTO_MODEL_B:
        return AUTO_MODEL_B
    if name and name in ALLOWED_MODELS:
        return name
    return default


def model_b_for_attempt(model_b_setting: str, attempt: int) -> str:
    """Resolves the model to use for one evaluator-loop attempt. Fixed
    settings just return themselves; 'auto' climbs ALLOWED_MODELS cheapest
    first, one tier per failed attempt, capped at the most expensive tier."""
    if model_b_setting != AUTO_MODEL_B:
        return model_b_setting
    return ALLOWED_MODELS[min(attempt, len(ALLOWED_MODELS) - 1)]


def settings_catalogue() -> dict:
    model_b_options = [{"id": m, "label": MODEL_LABELS[m]} for m in ALLOWED_MODELS]
    model_b_options.append({"id": AUTO_MODEL_B, "label": "Auto (cheapest that passes the evaluator)"})
    return {
        "allowed_models": [{"id": m, "label": MODEL_LABELS[m]} for m in ALLOWED_MODELS],
        "allowed_models_b": model_b_options,
        "defaults": PipelineSettings().model_dump(),
        "help": {
            "evaluator_threshold": THRESHOLD_HELP,
            "critic_enabled": CRITIC_HELP,
            "max_revision_rounds": REVISION_ROUNDS_HELP,
            "auto_model_b": AUTO_MODEL_B_HELP,
        },
    }
