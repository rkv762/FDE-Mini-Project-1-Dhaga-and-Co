"""Tests the pipeline's control flow (routing, grounding, evaluator-optimizer
retries) with the model calls stubbed out — no ANTHROPIC_API_KEY required.
Real model *behaviour* (prompt quality) is exercised manually via
scripts/demo.py against the live API, not here.
"""
from unittest.mock import patch

import pytest

from src import pipeline
from src.schemas import (
    CustomerProfileFacts,
    EvaluationResult,
    MessageDraft,
    ModelCall,
    PersonaDecision,
    PersonaSafetyCheck,
    SkuShortlist,
    SkuPick,
)
from src.settings import PipelineSettings

FREE_CALL = ModelCall(step="test", model="test-model", input_tokens=0, output_tokens=0, estimated_cost_usd=0.0)


@pytest.fixture(autouse=True)
def no_real_decision_log(monkeypatch):
    """Tests exercise pipeline.run() directly, which logs every call — keep
    that out of the real data/decision_log.jsonl."""
    monkeypatch.setattr(pipeline.data_access, "append_decision", lambda entry: entry)


def _profile(data_sufficient=True):
    return CustomerProfileFacts(
        preferred_categories=["womenswear"],
        price_signal="mid-range",
        occasion_phrases=["mehndi function dress"],
        fit_complaints=False,
        data_sufficient=data_sufficient,
        rationale="test",
    )


def test_insufficient_data_short_circuits_before_recommendation(monkeypatch):
    monkeypatch.setattr(pipeline.data_access, "fetch_customer_history",
                         lambda cid: {"customer_id": cid, "orders": [], "events": [], "returns": [], "reviews": []})
    monkeypatch.setattr(pipeline.chains, "extract_profile", lambda h, model_name=None: (_profile(False), FREE_CALL))
    monkeypatch.setattr(pipeline.chains, "route_persona",
                         lambda p, model_name=None: (PersonaDecision(persona="insufficient_data", reasoning="too little data"), FREE_CALL))

    with patch.object(pipeline.chains, "shortlist_skus") as shortlist_mock:
        result = pipeline.run("CUSTX")
        shortlist_mock.assert_not_called()

    assert result.status == "insufficient_data"
    assert result.recommended_skus == []


def test_hallucinated_sku_is_dropped_and_fails_visibly(monkeypatch):
    monkeypatch.setattr(pipeline.data_access, "fetch_customer_history",
                         lambda cid: {"customer_id": cid, "orders": [{"x": 1}], "events": [], "returns": [], "reviews": []})
    monkeypatch.setattr(pipeline.chains, "extract_profile", lambda h, model_name=None: (_profile(True), FREE_CALL))
    monkeypatch.setattr(pipeline.chains, "route_persona",
                         lambda p, model_name=None: (PersonaDecision(persona="occasion_driven", reasoning="ok"), FREE_CALL))
    monkeypatch.setattr(pipeline.data_access, "candidate_catalogue", lambda **kw: [])
    monkeypatch.setattr(pipeline.chains, "check_persona_safety",
                         lambda p, persona, h, model_name=None: (PersonaSafetyCheck(needs_human_review=False), FREE_CALL))
    monkeypatch.setattr(pipeline.chains, "shortlist_skus",
                         lambda p, persona, cat, model_name=None: (SkuShortlist(picks=[SkuPick(sku_id="SKU_DOES_NOT_EXIST", reason="x")]), FREE_CALL))
    monkeypatch.setattr(pipeline.data_access, "ground_skus", lambda ids: [])  # nothing survives grounding

    result = pipeline.run("CUSTX")
    assert result.status == "insufficient_data"
    assert "grounding" in result.reasons[0].lower()


def test_evaluator_retry_then_needs_human_review(monkeypatch):
    monkeypatch.setattr(pipeline.data_access, "fetch_customer_history",
                         lambda cid: {"customer_id": cid, "orders": [{"x": 1}], "events": [], "returns": [], "reviews": []})
    monkeypatch.setattr(pipeline.chains, "extract_profile", lambda h, model_name=None: (_profile(True), FREE_CALL))
    monkeypatch.setattr(pipeline.chains, "route_persona",
                         lambda p, model_name=None: (PersonaDecision(persona="occasion_driven", reasoning="ok"), FREE_CALL))
    monkeypatch.setattr(pipeline.data_access, "candidate_catalogue", lambda **kw: [])
    monkeypatch.setattr(pipeline.chains, "check_persona_safety",
                         lambda p, persona, h, model_name=None: (PersonaSafetyCheck(needs_human_review=False), FREE_CALL))
    monkeypatch.setattr(pipeline.chains, "shortlist_skus",
                         lambda p, persona, cat, model_name=None: (SkuShortlist(picks=[SkuPick(sku_id="SKU0001", reason="x")]), FREE_CALL))
    monkeypatch.setattr(pipeline.data_access, "ground_skus",
                         lambda ids: [{"sku_id": "SKU0001", "category": "womenswear", "price_inr": "599",
                                       "colour_raw": "Navy Blue", "in_stock": "True"}])
    monkeypatch.setattr(pipeline.chains, "draft_message",
                         lambda p, persona, skus, revision_notes="", model_name=None: (MessageDraft(channel="push", text="hi"), FREE_CALL))

    call_count = {"n": 0}

    def always_reject(persona, skus, message, model_name=None):
        call_count["n"] += 1
        return EvaluationResult(approved=False, score=0.2, reasons=["too generic"], revision_notes="be specific"), FREE_CALL

    monkeypatch.setattr(pipeline.chains, "evaluate_message", always_reject)

    settings = PipelineSettings(max_revision_rounds=2)
    result = pipeline.run("CUSTX", settings)
    assert result.status == "needs_human_review"
    assert call_count["n"] == settings.max_revision_rounds + 1


def test_evaluator_threshold_rejects_low_score_even_if_approved(monkeypatch):
    """A high evaluator_threshold should escalate even an 'approved' draft
    whose score doesn't clear the bar — the threshold, not just the model's
    own verdict, is what the settings panel's slider controls."""
    monkeypatch.setattr(pipeline.data_access, "fetch_customer_history",
                         lambda cid: {"customer_id": cid, "orders": [{"x": 1}], "events": [], "returns": [], "reviews": []})
    monkeypatch.setattr(pipeline.chains, "extract_profile", lambda h, model_name=None: (_profile(True), FREE_CALL))
    monkeypatch.setattr(pipeline.chains, "route_persona",
                         lambda p, model_name=None: (PersonaDecision(persona="occasion_driven", reasoning="ok"), FREE_CALL))
    monkeypatch.setattr(pipeline.data_access, "candidate_catalogue", lambda **kw: [])
    monkeypatch.setattr(pipeline.chains, "check_persona_safety",
                         lambda p, persona, h, model_name=None: (PersonaSafetyCheck(needs_human_review=False), FREE_CALL))
    monkeypatch.setattr(pipeline.chains, "shortlist_skus",
                         lambda p, persona, cat, model_name=None: (SkuShortlist(picks=[SkuPick(sku_id="SKU0001", reason="x")]), FREE_CALL))
    monkeypatch.setattr(pipeline.data_access, "ground_skus",
                         lambda ids: [{"sku_id": "SKU0001", "category": "womenswear", "price_inr": "599",
                                       "colour_raw": "Navy Blue", "in_stock": "True"}])
    monkeypatch.setattr(pipeline.chains, "draft_message",
                         lambda p, persona, skus, revision_notes="", model_name=None: (MessageDraft(channel="push", text="hi"), FREE_CALL))
    monkeypatch.setattr(pipeline.chains, "evaluate_message",
                         lambda persona, skus, message, model_name=None: (
                             EvaluationResult(approved=True, score=0.5, reasons=["fine but not great"]), FREE_CALL))

    result = pipeline.run("CUSTX", PipelineSettings(evaluator_threshold=0.9, max_revision_rounds=0))
    assert result.status == "needs_human_review"


def test_critic_disabled_skips_evaluation(monkeypatch):
    monkeypatch.setattr(pipeline.data_access, "fetch_customer_history",
                         lambda cid: {"customer_id": cid, "orders": [{"x": 1}], "events": [], "returns": [], "reviews": []})
    monkeypatch.setattr(pipeline.chains, "extract_profile", lambda h, model_name=None: (_profile(True), FREE_CALL))
    monkeypatch.setattr(pipeline.chains, "route_persona",
                         lambda p, model_name=None: (PersonaDecision(persona="occasion_driven", reasoning="ok"), FREE_CALL))
    monkeypatch.setattr(pipeline.data_access, "candidate_catalogue", lambda **kw: [])
    monkeypatch.setattr(pipeline.chains, "check_persona_safety",
                         lambda p, persona, h, model_name=None: (PersonaSafetyCheck(needs_human_review=False), FREE_CALL))
    monkeypatch.setattr(pipeline.chains, "shortlist_skus",
                         lambda p, persona, cat, model_name=None: (SkuShortlist(picks=[SkuPick(sku_id="SKU0001", reason="x")]), FREE_CALL))
    monkeypatch.setattr(pipeline.data_access, "ground_skus",
                         lambda ids: [{"sku_id": "SKU0001", "category": "womenswear", "price_inr": "599",
                                       "colour_raw": "Navy Blue", "in_stock": "True"}])
    monkeypatch.setattr(pipeline.chains, "draft_message",
                         lambda p, persona, skus, revision_notes="", model_name=None: (MessageDraft(channel="push", text="hi"), FREE_CALL))

    with patch.object(pipeline.chains, "evaluate_message") as eval_mock:
        result = pipeline.run("CUSTX", PipelineSettings(critic_enabled=False))
        eval_mock.assert_not_called()

    assert result.status == "ok"


def test_malformed_evaluator_output_escalates_not_ships(monkeypatch):
    """Regression test: a real run against OpenRouter once had the evaluator
    return score=3.5 (outside the declared 0-1 range), which raised a
    pydantic ValidationError inside chains.evaluate_message. That must
    escalate to needs_human_review, never silently fall through to 'ok' as
    if the message had been evaluated when it never actually was."""
    monkeypatch.setattr(pipeline.data_access, "fetch_customer_history",
                         lambda cid: {"customer_id": cid, "orders": [{"x": 1}], "events": [], "returns": [], "reviews": []})
    monkeypatch.setattr(pipeline.chains, "extract_profile", lambda h, model_name=None: (_profile(True), FREE_CALL))
    monkeypatch.setattr(pipeline.chains, "route_persona",
                         lambda p, model_name=None: (PersonaDecision(persona="occasion_driven", reasoning="ok"), FREE_CALL))
    monkeypatch.setattr(pipeline.data_access, "candidate_catalogue", lambda **kw: [])
    monkeypatch.setattr(pipeline.chains, "check_persona_safety",
                         lambda p, persona, h, model_name=None: (PersonaSafetyCheck(needs_human_review=False), FREE_CALL))
    monkeypatch.setattr(pipeline.chains, "shortlist_skus",
                         lambda p, persona, cat, model_name=None: (SkuShortlist(picks=[SkuPick(sku_id="SKU0001", reason="x")]), FREE_CALL))
    monkeypatch.setattr(pipeline.data_access, "ground_skus",
                         lambda ids: [{"sku_id": "SKU0001", "category": "womenswear", "price_inr": "599",
                                       "colour_raw": "Navy Blue", "in_stock": "True"}])
    monkeypatch.setattr(pipeline.chains, "draft_message",
                         lambda p, persona, skus, revision_notes="", model_name=None: (MessageDraft(channel="push", text="hi"), FREE_CALL))

    def always_malformed(persona, skus, message, model_name=None):
        raise ValueError("score 3.5 is not <= 1 (simulated ValidationError)")

    monkeypatch.setattr(pipeline.chains, "evaluate_message", always_malformed)

    result = pipeline.run("CUSTX", PipelineSettings(max_revision_rounds=1))
    assert result.status == "needs_human_review"
    assert any("malformed" in r.lower() for r in result.reasons)


def test_run_never_raises_on_unexpected_error(monkeypatch):
    def boom(cid):
        raise RuntimeError("simulated outage")

    monkeypatch.setattr(pipeline.data_access, "fetch_customer_history", boom)
    result = pipeline.run("CUSTX")
    assert result.status == "error"
    assert "simulated outage" in result.reasons[0]
