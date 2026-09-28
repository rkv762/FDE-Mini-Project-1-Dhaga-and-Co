"""Pydantic schemas for every model-call boundary in the pipeline.

Every LangChain structured-output call returns one of these, never free text
parsed downstream (per docs/architecture.md).
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

Persona = Literal[
    "occasion_driven",
    "price_sensitive",
    "fit_frustrated",
    "brand_loyal_dormant",
    "insufficient_data",
]


class CustomerProfileFacts(BaseModel):
    """Output of the extraction step (Model A)."""

    preferred_categories: list[str] = Field(
        description="Categories this customer buys or browses, e.g. womenswear, kidswear, mens"
    )
    price_signal: str = Field(
        description="One sentence on price sensitivity, grounded in the order/browse history given"
    )
    occasion_phrases: list[str] = Field(
        default_factory=list,
        description="Raw Hinglish/vernacular occasion search phrases seen in app events, e.g. 'mehndi function dress'",
    )
    fit_complaints: bool = Field(
        description="True if return history shows repeated fit/size complaints"
    )
    fit_evidence: Optional[str] = Field(default=None, description="Quote or paraphrase of the evidence, if any")
    review_sentiment: Optional[str] = Field(
        default=None, description="One sentence summary of sentiment in this customer's reviews, if any"
    )
    data_sufficient: bool = Field(
        description="False if there are fewer than 2 orders and no browse events"
    )
    rationale: str = Field(description="Why this profile was extracted this way, in one or two sentences")


class PersonaDecision(BaseModel):
    """Output of the routing step (Model A)."""

    persona: Persona
    reasoning: str


class PersonaSafetyCheck(BaseModel):
    """Parallel branch 4a (Model A): does this customer need a human-reviewed send?"""

    needs_human_review: bool
    reasons: list[str] = Field(default_factory=list)


class SkuPick(BaseModel):
    sku_id: str
    reason: str


class SkuShortlist(BaseModel):
    """Parallel branch 4b (Model A): ranked candidate SKUs."""

    picks: list[SkuPick] = Field(default_factory=list)


class GroundedSku(BaseModel):
    """Step 5 output (code, not a model): verified catalogue facts for a chosen SKU."""

    sku_id: str
    category: str
    price_inr: float
    colour: str
    in_stock: bool
    reason: str


class MessageDraft(BaseModel):
    """Step 6 output (Model B)."""

    channel: Literal["push", "whatsapp"]
    text: str


class EvaluationResult(BaseModel):
    """Step 7 output (Model B, evaluator-optimizer)."""

    approved: bool
    score: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    revision_notes: Optional[str] = Field(
        default=None, description="Set only when approved is False — fed back into the message draft step"
    )


class ModelCall(BaseModel):
    step: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0


class PipelineResult(BaseModel):
    status: Literal["ok", "insufficient_data", "needs_human_review", "error"]
    customer_id: str
    persona: Optional[Persona] = None
    recommended_skus: list[GroundedSku] = Field(default_factory=list)
    message: Optional[MessageDraft] = None
    evaluation: Optional[EvaluationResult] = None
    reasons: list[str] = Field(default_factory=list)
    model_calls: list[ModelCall] = Field(default_factory=list)
    estimated_cost_usd: float = 0.0
    model_b_used: Optional[str] = Field(
        default=None, description="Which model actually produced the final message — always the "
        "fixed Model B setting, unless Model B was 'auto', in which case this is whichever tier "
        "the cost-ascending cascade settled on."
    )
