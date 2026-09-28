# Build Note — Dhaga & Co.

_Two pages maximum. Code-vs-model, patterns and temperatures below are final. Cost and "what
broke" are filled in after a live run against the deployed URL — see the TODOs._

## Code vs. model

| Step | Deterministic code or model call | Why |
|---|---|---|
| [1] Fetch customer history | Code (`src/data_access.py`) | Pure lookup against the sample CSVs — no judgment involved. |
| Pre-filter candidate catalogue | Code (`data_access.candidate_catalogue`) | Cuts the SKU-ranking prompt down to in-stock, category-matched candidates before spending a model call on it. |
| [2] Extract `CustomerProfileFacts` | Model A | Free-text search terms, Hinglish, messy return reasons — needs language understanding, not string matching. |
| [3] Route persona | Model A | Classification from unstructured signals across orders/events/returns. |
| [4a] Persona safety check | Model A | Judging whether a recent bad experience makes an automated send inappropriate is a judgment call, not a lookup. |
| [4b] SKU shortlist | Model A | Semantic match against free-text catalogue attributes (colour typed a dozen ways, fabric as free text) — not exact lookup. |
| [5] Ground chosen SKUs | Code (`data_access.ground_skus`) | Verifies the model's picks against the catalogue and silently drops any invented `sku_id` — the one place hallucination is caught deterministically. |
| [6] Draft message | Model B | Customer-facing prose quality and tone matter; this is writing, not classification. |
| [7] Evaluate draft | Model B | Scoring relevance/tone/groundedness against a rubric needs judgment, not a fixed rule. |
| Evaluator retry bookkeeping | Code (`src/pipeline.py`) | Bounded loop (max 2 retries), schema validation, final status gating — deterministic control flow around the model calls. |
| Output gate (blocked-phrase check) | Code (`pipeline._output_gate`) | A deterministic backstop after the LLM evaluator approves — never trust a single model pass alone for what reaches the customer. |
| Decision log | Code (`data_access.append_decision`) | Every run is appended to `data/decision_log.jsonl` regardless of outcome — the audit trail the cost line below is computed from. |

## Patterns used

All four are used, deliberately, not just the required two — see
[`docs/architecture.md`](architecture.md) for the full design:

- **Prompt chaining** — steps [2]→[3]→[4]→[5]→[6]→[7] form a strict pipeline; each step needs
  the previous step's validated output. Without it, a single mega-prompt would have to hold
  extraction, routing and drafting logic at once, and errors couldn't be caught between steps.
- **Routing** (`RunnableBranch` in `src/pipeline.py`) — the insufficient-data short-circuit is
  a genuine branch: customers with too little history never reach the recommendation/message
  steps at all. Without it, the model would be tempted to guess a persona from thin evidence
  rather than admitting it doesn't know.
- **Parallelization** (`RunnableParallel` in `src/pipeline.py`) — the persona-safety check and
  the SKU shortlist share no data dependency, so they run concurrently. Without it, the two
  calls would simply add their latencies serially for no quality benefit.
- **Evaluator-optimizer** — a single-pass draft from Model B regularly needs a second look
  (off-tone, or referencing a fact not in the grounded SKU list); the evaluator catches this
  and feeds concrete revision notes back into the draft step, bounded at 2 retries before
  failing visibly to `needs_human_review`. Without it, the "safe to publish unread" ground rule
  in the brief would not hold.

## Models and cost

- **Model A** (Claude Haiku 4.5) — the high-volume steps: extraction, routing, persona safety,
  SKU shortlist. All are classification/extraction/matching tasks, run at temperature 0.
- **Model B** — by default, **Auto**: a cost-ascending cascade (`src/settings.py`,
  `model_b_for_attempt`), not a fixed model. The draft/evaluate steps try Haiku 4.5 first;
  only if the evaluator rejects it (or its own output is malformed — see "what broke") does
  the next attempt escalate to Sonnet 5, then Opus 5.5. A message never pays for a stronger
  model than it actually needed to clear the bar. Model B can still be pinned to one model via
  the Settings panel when the group wants deterministic, repeatable cost per pitch demo.

Running everything on the most expensive tier would be safe but not viable at Dhaga & Co.'s
volume; running everything on the cheapest would produce weaker customer-facing copy. Auto
mode is the direct answer to "pick the model automatically whose price is less and whose
output is still good enough" — and it's also a real lever on the growth story: a lower cost
per re-engagement message means more of the dormant customer base can be reached for the same
marketing budget, which is the whole point of fixing a stuck repeat-purchase rate rather than
just reporting on it.

Callable via either the Anthropic API directly or OpenRouter (`MODEL_PROVIDER` in `.env`) —
the team is running on a shared OpenRouter credit pool day to day, so both paths are wired up
rather than assuming a native Anthropic key. Same two underlying models either way.

Per-call cost is computed in `src/pricing.py` from each call's actual token usage (captured via
`with_structured_output(..., include_raw=True)`), summed into `estimated_cost_usd` on every
`PipelineResult`, and appended to `data/decision_log.jsonl` on every run — not estimated after
the fact.

### Why this split, in numbers

**Real measured run** (`python scripts/demo.py CUST0001`, 2026-09-28, `status: ok`, approved on
the first evaluator pass, no retries): **$0.01531** for 6 model calls (4 on Haiku 4.5, 2 on
Sonnet 5), ~7,772 total tokens. Recomputing the same 6 calls' actual token counts at each
scenario's rates:

| Scenario | Cost for this real run | Cost / week at 5,000 customers |
|---|---|---|
| All calls on Model A (Haiku 4.5) only | $0.01151 | $57.55 |
| **Our split** (A for extract/route/safety/shortlist, B for draft/evaluate) | **$0.01531** | **$76.55** |
| All calls on Model B (Sonnet 5) only | $0.02303 | $115.15 |

The split costs ~33% more than an all-Haiku pipeline but produced a message the evaluator
scored 0.92/1.0 and approved outright (Hinglish, occasion-grounded, COD mentioned, no
fabricated facts) — that quality bar is what the extra cost buys on the two steps that need
it. It costs ~34% less than running the stronger model on every step, including the four purely
classification/extraction ones with no quality benefit from doing so. The weekly batch size
(5,000) is still an assumption — the actual number is however many customers fall in Dhaga &
Co.'s dormant/repeat-purchase window, not all 48,000 weekly orders. `scripts/cost_comparison.py`
has the pre-deployment token-count estimates this real run came in above (estimates undercounted
prompt overhead — expect real costs to run ~2x the rough estimate, not the estimate itself).

**Real observed cost range**, now that both ends have been hit live: **$0.01531** best case
(`CUST0001`, approved first pass, no retries) to **$0.04211** worst case (`CUST0002`, hit the
malformed-output bug on Haiku, escalated through Sonnet and Opus, still ended in
`needs_human_review` — see "what broke"). The worst case is ~2.75x the best case, which is the
honest range to quote, not just the happy-path number.

## Temperatures

| Step | Model | Temperature | Why |
|---|---|---|---|
| Extract profile | A | 0 | Extraction should be repeatable, not creative. |
| Route persona | A | 0 | Classification, not generation. |
| Persona safety check | A | 0 | A judgment call that should be consistent across runs. |
| SKU shortlist | A | 0 | Ranking/matching against given candidates, not invention. |
| Draft message | B | 0.7 | The one genuinely generative, customer-facing step. |
| Evaluate draft | B | 0 | Scoring against a fixed rubric should be consistent, not creative. |

## What broke that we did not expect

- **Vercel's Python runtime dropped multi-file `/api/*.py` auto-detection.** The MVP was first
  built as `api/customers.py` + `api/recommend.py`, each its own `BaseHTTPRequestHandler` —
  the classic zero-config pattern. Current Vercel now wants a single ASGI/WSGI entrypoint
  (`app.py` exporting `app`); deploy failed with "No python entrypoint found" until we merged
  both into one FastAPI app. Worth knowing before the group assumes an old tutorial's Python
  deployment pattern still works as documented.
- **Vercel's deployed filesystem is read-only outside `/tmp`.** The decision log
  (`data_access.append_decision`) worked perfectly in every local run and every test, then
  broke the *entire* `/api/recommend` request in production the first time it was hit live —
  `OSError: [Errno 30] Read-only file system: '/var/task/data/decision_log.jsonl'`, a plain
  500 with no useful message reaching the browser. Caught via `vercel logs`, not by staring at
  the code. Fixed two ways: the log path switches to `/tmp` when `VERCEL` is set (ephemeral
  per invocation there, so not a durable log in production — a real limitation, not hidden),
  and `append_decision` now swallows `OSError` so a logging failure can never again take down
  the customer-facing result. The lesson that generalizes: side-effects that are harmless
  locally (writing a file) are not automatically harmless deployed, and a "nice to have" log
  should never be able to break the thing it's logging.
- **Vercel's serverless function duration limit.** A full run makes 4-7 sequential/parallel
  model calls; `vercel.json` raises `app.py` to a 60s `maxDuration`, but a cold function start
  plus an evaluator retry can still approach that ceiling. _(TODO: confirm against real
  observed latency once deployed with a real API key.)_
- **A model returned a score outside its own declared range, and the whole pipeline died for
  it.** First real run against the live API (`CUST0002`): the evaluator's structured output
  came back with `score: 3.5` — outside the `EvaluationResult` schema's `0 ≤ score ≤ 1`
  constraint — and the pydantic `ValidationError` raised straight out of
  `with_structured_output(...).invoke(...)`, past `_call_structured`'s own error handling,
  and the whole request died as `status: "error"` on a wasted API call. Worse than the crash
  itself: the initial fix attempt (catching the exception and leaving `evaluation = None`)
  would have shipped the message as `"ok"` — "no evaluation" and "evaluation passed" looked
  identical to the status logic. Fixed by tracking evaluator failures explicitly
  (`evaluation_errored`) and routing them to `needs_human_review`, never to a silent pass.
  Re-ran the same customer after the fix: the Haiku attempt hit the identical malformed
  output, retried instead of crashing, escalated to Sonnet (rejected — an ungrounded "shirt"
  claim for a kidswear item), escalated to Opus (still correctly declined to ship it), and
  the whole cascade cost $0.04211 — a real worst-case number, next to the $0.01531 best-case
  run from `CUST0001`. The lesson: a model violating its own contract is not a hypothetical
  edge case to hand-wave past in a schema comment — it happened on the very first live
  customer. Known gap this leaves: token usage from an attempt that raises isn't captured
  (LangChain never returns to `_call_structured`'s cost-tracking lines), so
  `estimated_cost_usd` slightly undercounts any run that hits this path — worth fixing before
  this cost line is quoted to the client as exact.
