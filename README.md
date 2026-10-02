# Next Purchase Nudge — Dhaga & Co.

*The Dhaga & Co. Engagement*

## What this is

**Next Purchase Nudge Engine** — an agentic LangChain pipeline that turns a Dhaga & Co.
customer's order history, browse/search behaviour and reviews into one personalized
re-engagement recommendation (product + message), evaluated before it ever reaches the
customer. Built to move the metric the CEO named directly: a repeat purchase rate stuck at
22% for six quarters. See [`docs/discovery-note.md`](docs/discovery-note.md) for the case and
[`docs/architecture.md`](docs/architecture.md) for the design.

The original client-engagement brief and reader's guide are kept local only — see
`.gitignore` — since they're proprietary client materials, not something to redistribute in
this repo.

## What it expects

- Python 3.10+
- Two Claude models, called via either **Anthropic directly** or **OpenRouter** — pick one in
  `.env` (`MODEL_PROVIDER=anthropic` or `openrouter`). The pipeline does nothing useful without
  a key for whichever provider you choose. Get an Anthropic key at
  [console.anthropic.com](https://console.anthropic.com), or an OpenRouter key at
  [openrouter.ai/keys](https://openrouter.ai/keys).
- No database: all data is the **synthetic sample data in `data/*.csv`** (see below) — 75
  customers, 150 SKUs, 224 orders — shaped like the brief's real numbers, not three hand-picked
  rows: 75 distinct colour spellings across the catalogue (the brief cites ~90 for the real
  14k-SKU catalogue), Hinglish/occasion search phrases in `app_events.csv`, and ~38% of returns
  landing in "Other" free text (brief: 44%). Regenerate with `python data/generate_mock_data.py`
  (seeded, reproducible).

## Running locally (cold start, no Vercel CLI needed)

```bash
git clone https://github.com/rkv762/dhaga-next-purchase-nudge.git
cd dhaga-next-purchase-nudge
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in .env: MODEL_PROVIDER + the matching API key
python scripts/demo.py --list          # see sample customer ids
python scripts/demo.py CUST0001        # run the pipeline for one customer, prints JSON
```

That's the whole cold start — clone, venv, install, key, run. No Postgres, no Vercel account,
no Streamlit server required to see it work.

## What it does when something goes wrong

The pipeline never crashes silently — every path returns a JSON result with a `status`:

| `status` | Means |
|---|---|
| `ok` | Recommendation + message generated and passed the evaluator. |
| `insufficient_data` | Customer has too little history to profile, or every recommended SKU turned out to be invented and was dropped by the grounding step — shown rather than guessed. |
| `needs_human_review` | The persona-safety check or the evaluator rejected the draft (e.g. a customer with a recent bad return experience) — a human should look before anything is sent. |
| `error` | Something unexpected (e.g. no API key set) — the reason is in `reasons`, never a bare stack trace to the caller. |

Run `python -m pytest` to see this exercised directly (routing short-circuit, hallucinated-SKU
drop, evaluator retry-then-fail, unexpected-exception path) — no API key needed, model calls
are stubbed.

## Deployed version

- **Live URL:** https://dhaga-next-purchase-nudge.vercel.app — fully live, `OPENROUTER_API_KEY` configured
- **Repo:** https://github.com/rkv762/dhaga-next-purchase-nudge — GitHub auto-deploy connected, pushes to `main` deploy automatically

Deployed on Vercel as a single FastAPI app (`app.py`) that serves `index.html` at `/` and
exposes `/api/customers` and `/api/recommend`, wrapping the same `src/pipeline.py` used by
`scripts/demo.py`. To deploy your own copy:

1. Import this repo in the [Vercel dashboard](https://vercel.com/new) (auto-detected as Python).
2. Set the environment variables from `.env.example` in the Vercel project settings
   (Project → Settings → Environment Variables): `MODEL_PROVIDER` plus either
   `OPENROUTER_API_KEY` or `ANTHROPIC_API_KEY` depending on which you set it to, and optionally
   `MODEL_A` / `MODEL_B` to override the defaults.
3. Deploy. `vercel.json` gives `app.py` up to 60s — a full run makes 4-7 sequential/parallel
   model calls, which can approach that on a cold function start.

## Repository contents

| Path | Purpose |
|---|---|
| [`docs/discovery-note.md`](docs/discovery-note.md) | The one-page discovery note — problem, owner, evidence, cost, success metric, ranked shortlist, biggest assumption — agreed **before** any code. |
| [`docs/architecture.md`](docs/architecture.md) | The agreed MVP design: prompt chaining, routing, parallelization, evaluator-optimizer — LangChain only, no LangGraph. |
| [`docs/build-note.md`](docs/build-note.md) | Two-page build note: code-vs-model table, the cost line, what broke. |
| `data/*.csv` + `data/generate_mock_data.py` | Synthetic, Dhaga & Co.-shaped sample data and the (seeded, reproducible) script that generated it. |
| `src/schemas.py` | Every Pydantic schema validated at a model-call boundary. |
| `src/data_access.py` | Deterministic data lookups — no model calls. |
| `src/chains.py` | The LangChain (LCEL) chains for each model step. |
| `src/pipeline.py` | Orchestration: `RunnableBranch` routing, `RunnableParallel` branch, evaluator-optimizer retry loop. |
| `src/review.py` | Human review decisions — approve, reject, or approve an edited draft — validated before they're recorded. |
| `scripts/demo.py` | Local, no-server entry point for the cold start above. |
| `scripts/cost_comparison.py` | Compares the cost of an all-Model-A, all-Model-B, and our actual split, for the "why this split" numbers in the build note. |
| `app.py` + `index.html` + `vercel.json` | The deployed version — single FastAPI entrypoint per Vercel's current Python runtime convention. |
| `tests/test_pipeline.py` | Control-flow tests with model calls stubbed — run without an API key. |

## Status

- [x] Discovery note agreed
- [x] Architecture agreed
- [x] MVP built, runs locally
- [x] Deployed to a live URL, fully functional
- [x] Build note finished (cost line + what broke, after a live run)
- [ ] Presentation delivered
