# Build Note — Dhaga & Co.

## Code vs. model

| Step | Code or model | Why |
|---|---|---|
| [1] Fetch customer history | Code | Pure lookup, no judgment. |
| Pre-filter candidate catalogue | Code | Cuts the prompt to in-stock, category-matched candidates before spending a call on it. |
| [2] Extract `CustomerProfileFacts` | Model A | Hinglish, free text, messy return reasons — needs language understanding. |
| [3] Route persona | Model A | Classification from unstructured signals. |
| [4a] Persona safety check | Model A | Judging whether a recent bad experience makes an automated send unsafe is a judgment call. |
| [4b] SKU shortlist | Model A | Semantic match against free-text attributes (colour typed a dozen ways) — not exact lookup. |
| [5] Ground chosen SKUs | Code | Verifies picks against the catalogue, drops any invented `sku_id` — where hallucination is caught. |
| [6] Draft message | Model B | Customer-facing prose; writing, not classification. |
| [7] Evaluate draft | Model B | Scoring tone/groundedness against a rubric needs judgment. |
| Evaluator retry bookkeeping, output gate, decision log | Code | Bounded retry loop, blocked-phrase backstop, audit log — deterministic control flow around the model calls. |

## Patterns used (all four, deliberately)

- **Prompt chaining** — [2]→[3]→[4]→[5]→[6]→[7] is a strict pipeline; each step needs the last
  one's validated output, and errors are caught between steps rather than inside one mega-prompt.
- **Routing** (`RunnableBranch`) — the insufficient-data short-circuit means customers with too
  little history never reach the recommendation step; the model never guesses a persona from
  thin evidence.
- **Parallelization** (`RunnableParallel`) — the safety check and SKU shortlist share no data
  dependency, so they run concurrently instead of adding latency for no benefit.
- **Evaluator-optimizer** — a single-pass draft regularly needs a second look; the evaluator
  feeds revision notes back into the draft step, bounded at 2 retries before failing visibly to
  `needs_human_review`. This is what "safe to publish unread" requires in practice.

## Models and cost

**Model A** = Haiku 4.5 for the four high-volume classification/matching steps, temp 0.
**Model B** defaults to **Auto**, not a fixed model — a cost-ascending cascade
(`model_b_for_attempt` in `src/settings.py`): draft/evaluate try Haiku first, escalating to
Sonnet 5 then Opus 5.5 only when the evaluator rejects the cheaper attempt. A message never
pays for a stronger model than it needed. Either model can be pinned in the Settings panel for
a repeatable-cost demo. Callable via Anthropic directly or OpenRouter (`MODEL_PROVIDER`); the
team runs on a shared OpenRouter pool day to day.

**Real observed cost, both ends hit live:** **$0.01531** best case (`CUST0001`, approved on
Haiku+Sonnet, first pass) to **$0.04211** worst case (`CUST0002`, hit the malformed-output bug
below, escalated through all three tiers, still correctly ended in `needs_human_review`) — a
~2.75x range, the honest number to quote rather than just the happy path. At 5,000
customers/week (an assumption — the real figure is whoever's due for re-engagement, not all
48,000 weekly orders), that's roughly **$75–$210/week**, trivial next to a 40% YoY rise in CAC.
Cheaper Model B tiers mean more customers reachable for the same budget — the direct link
between this engineering choice and the growth story.

## Temperatures

| Step | Model | Temp | Why |
|---|---|---|---|
| Extract / route / safety / shortlist | A | 0 | Extraction and classification should be repeatable. |
| Draft message | B | 0.7 | The one genuinely generative, customer-facing step. |
| Evaluate draft | B | 0 | Scoring against a fixed rubric should be consistent. |

## What broke that we did not expect

- **Vercel dropped multi-file `/api/*.py` auto-detection.** Built first as separate
  `api/customers.py` + `api/recommend.py` handlers — the classic zero-config pattern. Current
  Vercel wants one ASGI entrypoint; deploy failed until merged into a single `app.py`.
- **Vercel's deployed filesystem is read-only outside `/tmp`.** The decision log worked in every
  local run and test, then took down the *entire* `/api/recommend` request the first time it
  hit production (`OSError: read-only file system`) — caught via `vercel logs`, not the code.
  Fixed by routing the log to `/tmp` on Vercel (ephemeral there — a real limitation, not hidden)
  and making a logging failure structurally unable to break the customer-facing result again.
- **A model returned a score outside its own declared range, and the pipeline died for it.**
  First live run (`CUST0002`): the evaluator returned `score: 3.5` against a `0–1` schema; the
  `ValidationError` raised past our error handling and killed the request on a wasted call.
  The first fix attempt was worse than the bug — silently treating "evaluation errored" the same
  as "no evaluation needed" would have shipped an unevaluated message as `"ok"`. Fixed by
  tracking evaluator failures explicitly, always escalating them to `needs_human_review`, and
  retrying instead of crashing. Re-run after the fix: Haiku hit the same malformed output,
  retried cleanly, escalated through Sonnet and Opus, and still correctly declined to ship an
  ungrounded claim at every tier — $0.04211, the worst-case number above. Known gap: token cost
  from an attempt that raises isn't captured, so the cost line slightly undercounts runs that
  hit this path.
- **Vercel's function duration limit is close, not comfortable.** A full run makes 4-7
  sequential/parallel model calls against a 60s `maxDuration`; a cold start plus a retry can
  approach it — not yet timed out, but not far off either.
