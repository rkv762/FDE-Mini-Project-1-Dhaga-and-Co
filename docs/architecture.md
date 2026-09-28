# Architecture — Next Purchase Nudge Engine

_Design for the MVP, agreed before implementation. Built with LangChain (Runnables/LCEL),
deliberately not LangGraph — the group has covered LangChain, not LangGraph, and every pattern
below is expressible with `RunnableSequence`, `RunnableParallel`, `RunnableBranch`/routing
logic, and a bounded evaluator-optimizer retry loop, with no state-machine graph required._

## What it does

For a dormant or repeat-purchase-window customer, generates one personalized product
recommendation + one personalized re-engagement message (push/WhatsApp copy), evaluates the
draft against a rubric before it can go out, and fails visibly instead of guessing when the
customer has too little data.

## Pipeline

```
customer_id
   │
   ▼
[1] Fetch history             deterministic code, no model
   (orders, browse/search events, returns, catalogue)
   │
   ▼
[2] Extract CustomerProfile    Model A · prompt chaining, step 1
   (structured output: Pydantic schema)
   │
   ▼
[3] Route by persona           Model A · routing pattern
   (occasion-driven / price-sensitive / fit-frustrated / brand-loyal-dormant
    / insufficient-data → short-circuit exit here)
   │
   ▼
[4] Parallel branch            RunnableParallel · parallelization pattern
   ├─ 4a. Persona safety check   Model A — does this customer need a
   │        (cheap, judgment)     human-reviewed send rather than an
   │                              automated one (e.g. recent bad
   │                              return experience)?
   └─ 4b. SKU shortlist          Model A — ranks catalogue candidates
            (cheap, matching)     (deterministically pre-filtered by
                                   category/price in code) against the
                                   free-text profile
   (4a and 4b share no data dependency, so they run concurrently)
   │
   ▼
[5] Ground the SKUs            deterministic code, no model
   Look up true price/colour/stock for the chosen SKU ids in the
   catalogue; drop any id the model invented. Fails visibly
   (short-circuits to "insufficient_data") if none survive.
   │
   ▼
[6] Draft message              Model B · prompt chaining, step 2
   (stronger model, customer-facing copy, grounded in step 5's facts)
   │
   ▼
[7] Evaluator-optimizer         Model B · evaluator-optimizer pattern
   Score the draft against a rubric (relevance, tone, factual grounding,
   safety). approve → done · revise → feed reasons back into [6],
   max 2 retries, then fail visibly to "needs_human_review"
   │
   ▼
{status: "ok", recommended_skus, message, persona, confidence} · or
{status: "insufficient_data" | "needs_human_review", reasons}
```

Four patterns are used, deliberately, not just the required two:

- **Prompt chaining** — raw rows → structured profile → recommendation → message is a strict
  pipeline; each step needs the previous step's validated output.
- **Routing** — a single generic prompt cannot serve an occasion-driven Hinglish searcher and
  a fit-frustrated repeat-returner equally well; routing lets each persona get a tuned prompt.
- **Parallelization** — SKU matching, message drafting and the safety pre-check don't depend on
  each other, so running them concurrently cuts latency at no cost to quality.
- **Evaluator-optimizer** — a single LLM pass on customer-facing copy reliably produces
  off-tone or ungrounded messages often enough that a second, cheap evaluation pass measurably
  improves what actually gets sent — this is the step that keeps output "safe to publish
  unread."

## Code vs. model line

| Step | Type | Why |
|---|---|---|
| Fetch history | Code | Pure lookup, no judgment involved |
| Extract profile | Model (A) | Free-text search terms, Hinglish, messy return reasons — needs language understanding |
| Route persona | Model (A) | Classification from unstructured signals |
| SKU shortlist | Model (A) | Semantic match against 60-odd free-text catalogue attributes, not exact lookup |
| Message draft | Model (B) | Customer-facing language quality and tone matter |
| Safety pre-check | Model (A) | Bounded classification task, not open-ended judgment |
| Evaluator | Model (B) | Judging quality/tone/groundedness needs the stronger model |
| Retry bookkeeping, schema validation, final gating | Code | Deterministic control flow |

## Two models, and why

- **Model A (cheap/fast)** — high-volume steps: extraction, routing, SKU matching, safety
  pre-check. Run at temperature 0 (these are classification/extraction tasks, not creative
  ones).
- **Model B (stronger)** — the two steps where judgment and language quality matter: the
  message draft (moderate temperature, it's customer-facing prose) and the evaluator
  (temperature 0, it's scoring against a rubric, not writing).

Running everything on Model B is safe but too expensive at Dhaga & Co.'s volume; running
everything on Model A produces weak customer-facing copy. The split gets both. The build note
will report the actual per-run cost and extrapolate it to the targeted weekly re-engagement
batch (dormant/repeat-window customers, not all 48,000 orders/week).

## Structured output and failure modes

Every model boundary returns a Pydantic-validated schema (`CustomerProfile`, `Recommendation`,
`EvaluationResult`), not free text parsed downstream. If extraction or validation fails, or the
customer has fewer than two orders and no browse events, the pipeline returns
`insufficient_data` rather than a low-confidence guess — visible failure over silent bad output.

## Real-shaped input

Search terms and return reasons are handled as they actually arrive: Hinglish
("mehndi function dress", "office wear kurti"), free text, and a catalogue where colour has
been typed many different ways. The sample dataset (`data/generate_mock_data.py`) is sized to
actually exercise this rather than gesture at it — 150 SKUs, 75 distinct colour spellings
(the brief cites ~90 for the real 14k-SKU catalogue), 75 customers, 224 orders, and ~38% of
returns landing in an unstructured "Other" bucket (brief: 44%). The extraction step (not string
matching) is what makes this tractable.

## Tunable settings (Settings panel + GET/PUT-style API)

`src/settings.py` defines `PipelineSettings` — model choice and evaluator strictness, exposed
in the UI's Settings panel and via `GET /api/settings` (also visible, and callable, at `/docs`
— FastAPI generates that for free from `app.py`'s routes):

- **Model A / Model B selectors** — exactly three models on offer, not an open list: Haiku 4.5,
  Sonnet 5, Opus 5.5. Model A defaults to Haiku (the four bulk steps). **Model B defaults to
  "Auto"**, not a fixed model: each evaluator-loop attempt escalates one tier up the same
  cheapest-to-priciest list, only when the previous tier's draft was rejected (see
  `model_b_for_attempt` in `src/settings.py`) — the cascade never pays for a stronger model
  than a message actually needed to clear the evaluator bar. Any single model can still be
  pinned in the Settings panel for a repeatable-cost demo run.
- **Evaluator threshold (0-1, default 0.7)** — a draft ships only if the evaluator both
  approves it *and* its score clears this bar. Low values approve more drafts on a weaker bar
  (cheaper, faster, less safe); high values push more drafts into revision or
  `needs_human_review` (safer, slower, more human load).
- **Critic loop enabled (default on)** — turning it off skips the evaluator entirely, so the
  first draft ships with no safety net at all.
- **Max revision rounds (0-4, default 2)** — how many times the evaluator-optimizer loop
  retries before giving up and escalating.

These are passed **per request, not held as server state** — every `/api/recommend` call
carries its own settings. That's a deliberate consequence of the read-only-filesystem lesson
in `docs/build-note.md`: Vercel's deployed functions don't reliably share memory across
requests either, so a mutable server-side `SETTINGS` dict would be a second version of the same
mistake. "No restart needed" in the UI copy is true here by construction.
