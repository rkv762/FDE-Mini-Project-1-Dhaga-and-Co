"""LangChain (LCEL) chains for every model-call step in docs/architecture.md.

Uses `langchain-anthropic` + LangChain's Runnable primitives only:
RunnableSequence (`|`, prompt chaining), RunnableParallel (parallelization),
RunnableBranch (routing), and a bounded retry loop (evaluator-optimizer).
Deliberately no LangGraph.
"""
import os

from langchain_core.prompts import ChatPromptTemplate

from src.pricing import estimate_cost_usd
from src.schemas import (
    CustomerProfileFacts,
    EvaluationResult,
    MessageDraft,
    ModelCall,
    PersonaDecision,
    PersonaSafetyCheck,
    SkuShortlist,
)

# Which provider to call — see .env.example. Defaults to OpenRouter: it's the
# provider this team actually has credits on, and both are wired up so a
# teammate with a native Anthropic key can still use one.
MODEL_PROVIDER = os.environ.get("MODEL_PROVIDER", "openrouter").strip().lower()

_DEFAULT_MODEL_A = {"anthropic": "claude-haiku-4-5-20251001", "openrouter": "anthropic/claude-haiku-4.5"}
_DEFAULT_MODEL_B = {"anthropic": "claude-sonnet-5", "openrouter": "anthropic/claude-sonnet-5"}

MODEL_A_NAME = os.environ.get("MODEL_A", _DEFAULT_MODEL_A.get(MODEL_PROVIDER, _DEFAULT_MODEL_A["anthropic"]))
MODEL_B_NAME = os.environ.get("MODEL_B", _DEFAULT_MODEL_B.get(MODEL_PROVIDER, _DEFAULT_MODEL_B["anthropic"]))

PERSONA_GUIDANCE = {
    "occasion_driven": "This customer shops around specific occasions (weddings, festivals, "
    "school, work). Lead the message with the occasion, in the same Hinglish/vernacular phrasing "
    "they searched with.",
    "price_sensitive": "This customer buys in the lowest price bands and searches with words like "
    "'sasta'/'budget'/'discount'. Lead with value and price, plainly stated.",
    "fit_frustrated": "This customer has returned items for size/fit reasons before. Be explicit "
    "about fit/size guidance and acknowledge the past issue briefly and respectfully — do not "
    "oversell.",
    "brand_loyal_dormant": "This customer bought repeatedly in the past but has gone quiet. "
    "Re-engage warmly, referencing that they're a returning customer, without sounding like a "
    "generic blast.",
}


def _model(name: str, temperature: float):
    if MODEL_PROVIDER == "openrouter":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=name,
            temperature=temperature,
            api_key=os.environ.get("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://github.com/rkv762/dhaga-next-purchase-nudge",
                "X-Title": "Dhaga & Co. Next Purchase Nudge",
            },
            timeout=30,
            max_retries=1,
        )
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=name, temperature=temperature, timeout=30, max_retries=1)


def _fmt_rows(rows: list[dict], empty_label: str = "(none)") -> str:
    if not rows:
        return empty_label
    lines = []
    for row in rows:
        lines.append(", ".join(f"{k}={v}" for k, v in row.items()))
    return "\n".join(lines)


def _call_structured(model, prompt: ChatPromptTemplate, schema, variables: dict, step: str):
    """Invoke a structured-output chain and return (parsed_object, ModelCall)."""
    chain = prompt | model.with_structured_output(schema, include_raw=True)
    result = chain.invoke(variables)
    parsed = result["parsed"]
    raw = result["raw"]
    usage = getattr(raw, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    call = ModelCall(
        step=step,
        model=model.model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=estimate_cost_usd(model.model, input_tokens, output_tokens),
    )
    if parsed is None:
        raise ValueError(f"{step}: model did not return a schema-valid response ({result.get('parsing_error')})")
    return parsed, call


# ---------------------------------------------------------------------------
# [2] Extraction (Model A) — prompt chaining, step 1
# ---------------------------------------------------------------------------
EXTRACTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You extract a structured customer profile for Dhaga & Co., a D2C fashion brand in "
            "India. Customers often search in Hinglish (Hindi+English) and describe needs by "
            "occasion, not product name. Ground every field only in the data given below — never "
            "invent orders, events or returns that are not listed.",
        ),
        (
            "human",
            "Customer: {customer_id}\n\n"
            "Orders:\n{orders}\n\n"
            "App search events:\n{events}\n\n"
            "Returns:\n{returns}\n\n"
            "Reviews written by this customer:\n{reviews}\n\n"
            "Extract a CustomerProfileFacts. Set data_sufficient to false if there are fewer than "
            "2 orders AND no browse events.",
        ),
    ]
)


def extract_profile(history: dict, model_name: str = None):
    model = _model(model_name or MODEL_A_NAME, temperature=0)
    variables = {
        "customer_id": history["customer_id"],
        "orders": _fmt_rows(history["orders"]),
        "events": _fmt_rows(history["events"]),
        "returns": _fmt_rows(history["returns"]),
        "reviews": _fmt_rows(history["reviews"]),
    }
    return _call_structured(model, EXTRACTION_PROMPT, CustomerProfileFacts, variables, "extract_profile")


# ---------------------------------------------------------------------------
# [3] Routing (Model A) — routing pattern
# ---------------------------------------------------------------------------
ROUTING_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You classify a Dhaga & Co. customer into exactly one persona so the right "
            "downstream re-engagement strategy is used. Personas: occasion_driven, "
            "price_sensitive, fit_frustrated, brand_loyal_dormant, insufficient_data. Use "
            "insufficient_data whenever the profile says data_sufficient is false — never guess "
            "a behavioural persona from too little evidence.",
        ),
        ("human", "CustomerProfileFacts:\n{profile}\n\nClassify the persona."),
    ]
)


def route_persona(profile: CustomerProfileFacts, model_name: str = None):
    model = _model(model_name or MODEL_A_NAME, temperature=0)
    variables = {"profile": profile.model_dump_json(indent=2)}
    return _call_structured(model, ROUTING_PROMPT, PersonaDecision, variables, "route_persona")


# ---------------------------------------------------------------------------
# [4a] Persona safety check (Model A) — parallel branch, half of parallelization pattern
# ---------------------------------------------------------------------------
SAFETY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You decide whether an automated re-engagement message is appropriate for this "
            "customer, or whether a human should review before anything is sent. Flag for human "
            "review on signs of a recent bad experience (e.g. multiple quality-related returns, "
            "negative reviews) — sending an automated 'come back!' message right after a bad "
            "experience can make things worse.",
        ),
        (
            "human",
            "CustomerProfileFacts:\n{profile}\n\nPersona: {persona}\n\nReturns on record:\n{returns}\n\n"
            "Decide needs_human_review.",
        ),
    ]
)


def check_persona_safety(profile: CustomerProfileFacts, persona: str, history: dict, model_name: str = None):
    model = _model(model_name or MODEL_A_NAME, temperature=0)
    variables = {
        "profile": profile.model_dump_json(indent=2),
        "persona": persona,
        "returns": _fmt_rows(history["returns"]),
    }
    return _call_structured(model, SAFETY_PROMPT, PersonaSafetyCheck, variables, "persona_safety_check")


# ---------------------------------------------------------------------------
# [4b] SKU shortlist (Model A) — parallel branch, other half of parallelization pattern
# ---------------------------------------------------------------------------
SKU_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You pick at most 3 products to recommend to this customer from the candidates "
            "given. Only ever pick sku_id values that appear in the candidate list — never "
            "invent one. {persona_guidance}",
        ),
        (
            "human",
            "CustomerProfileFacts:\n{profile}\n\nPersona: {persona}\n\n"
            "Candidate catalogue (pre-filtered by category/price):\n{catalogue}\n\n"
            "Pick the best matches.",
        ),
    ]
)


def shortlist_skus(profile: CustomerProfileFacts, persona: str, catalogue_candidates: list[dict],
                    model_name: str = None):
    model = _model(model_name or MODEL_A_NAME, temperature=0)
    variables = {
        "profile": profile.model_dump_json(indent=2),
        "persona": persona,
        "persona_guidance": PERSONA_GUIDANCE.get(persona, ""),
        "catalogue": _fmt_rows(catalogue_candidates),
    }
    return _call_structured(model, SKU_PROMPT, SkuShortlist, variables, "shortlist_skus")


# ---------------------------------------------------------------------------
# [6] Message draft (Model B) — prompt chaining, step 2
# ---------------------------------------------------------------------------
MESSAGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You write a short push/WhatsApp re-engagement message for a Dhaga & Co. customer. "
            "Most customers are on low-end Android phones with patchy connections and pay cash "
            "on delivery — keep it short, mention COD is available, and never state a price or "
            "colour that isn't in the grounded product facts given. {persona_guidance}\n\n"
            "If revision_notes are given, they are feedback from a reviewer on your previous "
            "draft — fix exactly those issues.",
        ),
        (
            "human",
            "Persona: {persona}\n\nGrounded product facts (the only facts you may reference):\n"
            "{grounded_skus}\n\nOccasion phrases this customer has searched for: {occasion_phrases}\n\n"
            "Revision notes (empty if this is the first draft): {revision_notes}\n\n"
            "Write the message.",
        ),
    ]
)


def draft_message(profile: CustomerProfileFacts, persona: str, grounded_skus: list[dict],
                   revision_notes: str = "", model_name: str = None):
    model = _model(model_name or MODEL_B_NAME, temperature=0.7)
    variables = {
        "persona": persona,
        "persona_guidance": PERSONA_GUIDANCE.get(persona, ""),
        "grounded_skus": _fmt_rows(grounded_skus),
        "occasion_phrases": ", ".join(profile.occasion_phrases) or "(none)",
        "revision_notes": revision_notes or "(none — first draft)",
    }
    return _call_structured(model, MESSAGE_PROMPT, MessageDraft, variables, "draft_message")


# ---------------------------------------------------------------------------
# [7] Evaluator (Model B) — evaluator-optimizer pattern
# ---------------------------------------------------------------------------
EVALUATOR_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You score a draft re-engagement message against a rubric before it can be sent. "
            "Approve only if ALL hold: (1) relevant to the stated persona, (2) tone is warm and "
            "not pushy, (3) every price/colour/product claim in the message matches the grounded "
            "product facts exactly — no invented facts, (4) it mentions COD is available, "
            "(5) it is short enough for a push notification or WhatsApp message. If you reject, "
            "revision_notes must state exactly what to fix.",
        ),
        (
            "human",
            "Persona: {persona}\n\nGrounded product facts:\n{grounded_skus}\n\nDraft message "
            "({channel}):\n{text}",
        ),
    ]
)


def evaluate_message(persona: str, grounded_skus: list[dict], message: MessageDraft, model_name: str = None):
    model = _model(model_name or MODEL_B_NAME, temperature=0)
    variables = {
        "persona": persona,
        "grounded_skus": _fmt_rows(grounded_skus),
        "channel": message.channel,
        "text": message.text,
    }
    return _call_structured(model, EVALUATOR_PROMPT, EvaluationResult, variables, "evaluate_message")
