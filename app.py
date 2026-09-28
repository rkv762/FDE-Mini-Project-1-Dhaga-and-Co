"""Single Vercel Python entrypoint (ASGI/FastAPI), per Vercel's current
Python runtime convention: https://vercel.com/docs/functions/runtimes/python

Serves the static demo page and the API routes it calls, wrapping the same
src/pipeline.py used by scripts/demo.py for local runs. Every endpoint here
also shows up "for free" in FastAPI's auto-generated /docs (Swagger UI) —
that's not a separate thing to build.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse

from src import chains, data_access, pipeline  # noqa: E402
from src.settings import ALLOWED_MODELS, PipelineSettings, resolve_model, settings_catalogue  # noqa: E402

app = FastAPI(title="Next Purchase Nudge — Dhaga & Co.")

ROOT_DIR = Path(__file__).resolve().parent
INDEX_HTML_PATH = ROOT_DIR / "index.html"


@app.on_event("startup")
def print_startup_links():
    print("\n" + "=" * 55)
    print("NEXT PURCHASE NUDGE — DHAGA & CO. IS LIVE")
    print("=" * 55)
    print("Demo:   http://127.0.0.1:8000")
    print("Docs:   http://127.0.0.1:8000/docs")
    print(f"Model provider: {chains.MODEL_PROVIDER}")
    print(f"Allowed models: {', '.join(ALLOWED_MODELS)}")
    print("=" * 55 + "\n")


@app.get("/api/health")
def health():
    return {"status": "ok", "provider": chains.MODEL_PROVIDER, "model_a": chains.MODEL_A_NAME,
            "model_b": chains.MODEL_B_NAME}


@app.get("/api/customers")
def customers():
    return data_access.list_customer_ids()


@app.get("/api/settings")
def get_settings():
    """The model choices, defaults and low/high help text the UI's Settings
    panel renders — one source of truth shared by the frontend and this API,
    not duplicated in JS."""
    return settings_catalogue()


@app.get("/api/recommend")
def recommend(
    customer_id: str = Query(..., min_length=1),
    model_a: str = Query(None, description="One of the ids from GET /api/settings.allowed_models"),
    model_b: str = Query(None, description="One of the ids from GET /api/settings.allowed_models"),
    evaluator_threshold: float = Query(None, ge=0.0, le=1.0),
    critic_enabled: bool = Query(None),
    max_revision_rounds: int = Query(None, ge=0, le=4),
):
    """Settings are per-request, not server-side state — see src/settings.py
    for why (Vercel's deployed functions don't reliably share memory across
    requests, so we don't pretend they do)."""
    defaults = PipelineSettings()
    settings = PipelineSettings(
        model_a=resolve_model(model_a, defaults.model_a),
        model_b=resolve_model(model_b, defaults.model_b, allow_auto=True),
        evaluator_threshold=evaluator_threshold if evaluator_threshold is not None else defaults.evaluator_threshold,
        critic_enabled=critic_enabled if critic_enabled is not None else defaults.critic_enabled,
        max_revision_rounds=max_revision_rounds if max_revision_rounds is not None else defaults.max_revision_rounds,
    )
    result = pipeline.run(customer_id, settings)
    payload = result.model_dump()
    # The raw evidence behind the recommendation — not part of PipelineResult
    # (it's not something a model produced), but a reviewer deciding whether
    # to trust or override the output needs to see it next to the output,
    # not take the model's word for it. Pure lookup, no extra model cost.
    payload["customer_history"] = data_access.fetch_customer_history(customer_id)
    return JSONResponse(content=payload, status_code=500 if result.status == "error" else 200)


@app.get("/")
def serve_frontend():
    return FileResponse(INDEX_HTML_PATH)
