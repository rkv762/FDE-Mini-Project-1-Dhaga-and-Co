"""Orchestrates the Next Purchase Nudge pipeline end to end (docs/architecture.md).

customer_id -> PipelineResult

Composition uses LangChain's Runnable primitives directly:
- RunnableSequence (the default `|` chaining, used inside src/chains.py) for
  prompt chaining.
- RunnableBranch for the insufficient-data routing short-circuit.
- RunnableParallel for the persona-safety-check / SKU-shortlist branch.
- A bounded loop for the evaluator-optimizer retry.
No LangGraph.

Every tunable (which model, the evaluator threshold, whether the critic loop
runs, how many revision rounds) comes in via PipelineSettings — see
src/settings.py for why this is passed per-call rather than held as server
state.
"""
from langchain_core.runnables import RunnableBranch, RunnableLambda, RunnableParallel

from src import chains, data_access
from src.schemas import GroundedSku, ModelCall, PipelineResult
from src.settings import PipelineSettings, model_b_for_attempt

# ── Output gate ──────────────────────────────────────────────────────────
# A deterministic backstop after the LLM evaluator approves a draft — never
# trust a single model pass alone for what reaches the customer. Catches
# overpromising language the evaluator might still let through.
BLOCKED_PHRASES = ["guaranteed", "100% off", "free gift", "cashback", "lifetime"]


def output_gate(text: str) -> list[str]:
    lowered = text.lower()
    return [p for p in BLOCKED_PHRASES if p in lowered]


def _to_grounded(rows: list[dict], picks_by_id: dict[str, str]) -> list[GroundedSku]:
    grounded = []
    for row in rows:
        grounded.append(
            GroundedSku(
                sku_id=row["sku_id"],
                category=row["category"],
                price_inr=float(row["price_inr"]),
                colour=row["colour_raw"],
                in_stock=row["in_stock"] == "True",
                reason=picks_by_id.get(row["sku_id"], ""),
            )
        )
    return grounded


def _run_pipeline(customer_id: str, settings: PipelineSettings) -> PipelineResult:
    model_calls: list[ModelCall] = []

    # [1] deterministic fetch — no model
    history = data_access.fetch_customer_history(customer_id)

    # [2] extraction — Model A, prompt chaining step 1
    profile, call = chains.extract_profile(history, model_name=settings.model_a)
    model_calls.append(call)

    # [3] routing — Model A
    decision, call = chains.route_persona(profile, model_name=settings.model_a)
    model_calls.append(call)
    persona = decision.persona

    def _insufficient_data(_):
        return PipelineResult(
            status="insufficient_data",
            customer_id=customer_id,
            persona=persona,
            reasons=[decision.reasoning],
            model_calls=model_calls,
            estimated_cost_usd=sum(c.estimated_cost_usd for c in model_calls),
        )

    def _continue_pipeline(_):
        return _run_after_routing(customer_id, history, profile, persona, model_calls, settings)

    # RunnableBranch: the routing short-circuit itself is a genuine branch —
    # insufficient-data customers never reach the recommendation/message steps.
    router = RunnableBranch(
        (lambda _: persona == "insufficient_data", RunnableLambda(_insufficient_data)),
        RunnableLambda(_continue_pipeline),
    )
    return router.invoke(None)


def _run_after_routing(customer_id, history, profile, persona, model_calls: list[ModelCall],
                        settings: PipelineSettings) -> PipelineResult:
    # [4] parallel branch — persona safety check and SKU shortlist share no
    # data dependency, so RunnableParallel runs them concurrently.
    candidates = data_access.candidate_catalogue(categories=profile.preferred_categories or None, limit=20)

    parallel = RunnableParallel(
        safety=RunnableLambda(lambda _: chains.check_persona_safety(
            profile, persona, history, model_name=settings.model_a)),
        shortlist=RunnableLambda(lambda _: chains.shortlist_skus(
            profile, persona, candidates, model_name=settings.model_a)),
    )
    branch_result = parallel.invoke(None)
    safety, safety_call = branch_result["safety"]
    shortlist, shortlist_call = branch_result["shortlist"]
    model_calls.extend([safety_call, shortlist_call])

    # [5] grounding — deterministic code, no model. Drops any hallucinated sku_id.
    picks_by_id = {p.sku_id: p.reason for p in shortlist.picks}
    grounded_rows = data_access.ground_skus(list(picks_by_id.keys()))
    grounded_skus = _to_grounded(grounded_rows, picks_by_id)

    if not grounded_skus:
        return PipelineResult(
            status="insufficient_data",
            customer_id=customer_id,
            persona=persona,
            reasons=["No valid SKUs survived grounding against the catalogue."],
            model_calls=model_calls,
            estimated_cost_usd=sum(c.estimated_cost_usd for c in model_calls),
        )

    grounded_dicts = [
        {"sku_id": s.sku_id, "category": s.category, "price_inr": s.price_inr, "colour_raw": s.colour,
         "in_stock": str(s.in_stock)}
        for s in grounded_skus
    ]

    # [6]+[7] evaluator-optimizer loop — Model B drafts, Model B evaluates
    # (unless critic_enabled is off), feed critique back into the draft,
    # bounded at settings.max_revision_rounds retries, then fail visibly.
    # "Approved" requires both the model's own verdict AND the score clearing
    # settings.evaluator_threshold — the threshold is the real lever: low
    # values let more drafts through on a weaker bar, high values force more
    # revision/escalation. See src/settings.py's THRESHOLD_HELP.
    #
    # When settings.model_b is "auto", each attempt climbs one tier up
    # ALLOWED_MODELS (cheapest first) instead of retrying the same model —
    # a cost-ascending cascade, not just a revision loop. A cheap-but-wrong
    # draft escalates to a stronger model; a cheap-and-right draft never
    # pays for one it didn't need.
    # A malformed structured-output response (e.g. an evaluator score outside
    # the declared 0-1 range — a real thing we saw a model do) must never be
    # treated as "no evaluation happened, ship it": that would ship an
    # unevaluated message as if it were approved. Track failures explicitly
    # and feed them back as a revision note like any other rejection, rather
    # than letting the exception abort the whole customer interaction.
    revision_notes = ""
    message = None
    evaluation = None
    model_used = None
    evaluation_errored = False
    for attempt in range(settings.max_revision_rounds + 1):
        model_used = model_b_for_attempt(settings.model_b, attempt)
        try:
            message, draft_call = chains.draft_message(
                profile, persona, grounded_dicts, revision_notes, model_name=model_used)
            model_calls.append(draft_call)

            if not settings.critic_enabled:
                evaluation = None
                evaluation_errored = False
                break

            evaluation, eval_call = chains.evaluate_message(persona, grounded_dicts, message, model_name=model_used)
            model_calls.append(eval_call)
        except Exception as exc:  # noqa: BLE001 - malformed model output, retry as a rejection
            evaluation = None
            evaluation_errored = True
            revision_notes = f"Previous attempt failed validation ({type(exc).__name__}) — retry carefully."
            continue

        evaluation_errored = False
        if evaluation.approved and evaluation.score >= settings.evaluator_threshold:
            break
        revision_notes = evaluation.revision_notes or "; ".join(evaluation.reasons)

    cost = sum(c.estimated_cost_usd for c in model_calls)
    gate_hits = output_gate(message.text) if message else []
    evaluation_failed = evaluation is not None and not (
        evaluation.approved and evaluation.score >= settings.evaluator_threshold
    )

    if safety.needs_human_review or evaluation_failed or evaluation_errored or gate_hits:
        reasons = []
        if safety.needs_human_review:
            reasons.extend(safety.reasons)
        if evaluation_failed:
            reasons.extend(evaluation.reasons or [f"Evaluator score below threshold {settings.evaluator_threshold}"])
        if evaluation_errored:
            reasons.append("Evaluator kept returning malformed output — escalated rather than shipped unevaluated.")
        if gate_hits:
            reasons.append(f"Output gate blocked phrase(s): {gate_hits}")
        return PipelineResult(
            status="needs_human_review",
            customer_id=customer_id,
            persona=persona,
            recommended_skus=grounded_skus,
            message=message,
            evaluation=evaluation,
            reasons=reasons,
            model_calls=model_calls,
            estimated_cost_usd=cost,
            model_b_used=model_used,
        )

    return PipelineResult(
        status="ok",
        customer_id=customer_id,
        persona=persona,
        recommended_skus=grounded_skus,
        message=message,
        evaluation=evaluation,
        model_calls=model_calls,
        estimated_cost_usd=cost,
        model_b_used=model_used,
    )


def run(customer_id: str, settings: PipelineSettings = None) -> PipelineResult:
    """Public entry point. Fails visibly: never raises for expected data-shape
    issues, always returns a PipelineResult with a status. Every call is
    appended to data/decision_log.jsonl regardless of outcome — that log is
    what the build note's cost line is computed from."""
    settings = settings or PipelineSettings()
    try:
        result = _run_pipeline(customer_id, settings)
    except Exception as exc:  # noqa: BLE001 - deliberate: fail visibly, not silently
        result = PipelineResult(
            status="error",
            customer_id=customer_id,
            reasons=[f"{type(exc).__name__}: {exc}"],
        )

    data_access.append_decision(
        {
            "customer_id": result.customer_id,
            "status": result.status,
            "persona": result.persona,
            "model_call_count": len(result.model_calls),
            "estimated_cost_usd": result.estimated_cost_usd,
            "settings": settings.model_dump(),
        }
    )
    return result
