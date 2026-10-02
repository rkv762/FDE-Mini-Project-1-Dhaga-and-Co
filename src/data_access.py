"""Deterministic data access — step [1] and step [5] in docs/architecture.md.

No model calls here. Pure lookups against the sample CSVs in data/. Swapping
this module for real Postgres/Mixpanel/Freshdesk queries is the only change
needed to point the pipeline at Dhaga & Co.'s real systems.
"""
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_cache: dict[str, list[dict]] = {}


def _read_csv(name: str) -> list[dict]:
    if name not in _cache:
        path = DATA_DIR / name
        with open(path, newline="", encoding="utf-8") as f:
            _cache[name] = list(csv.DictReader(f))
    return _cache[name]


def fetch_customer_history(customer_id: str) -> dict:
    """Step [1]: pull everything known about one customer. Pure lookup."""
    orders = [o for o in _read_csv("orders.csv") if o["customer_id"] == customer_id]
    events = [e for e in _read_csv("app_events.csv") if e["customer_id"] == customer_id]
    returns = [r for r in _read_csv("returns.csv") if r["customer_id"] == customer_id]
    reviews = [r for r in _read_csv("reviews.csv") if r["customer_id"] == customer_id]
    customer = next((c for c in _read_csv("customers.csv") if c["customer_id"] == customer_id), None)
    return {
        "customer_id": customer_id,
        "customer": customer,
        "orders": orders,
        "events": events,
        "returns": returns,
        "reviews": reviews,
    }


def candidate_catalogue(categories: Optional[list[str]] = None, max_price: Optional[float] = None,
                         limit: int = 25) -> list[dict]:
    """Cheap pre-filter before the SKU-ranking model call — keeps the prompt small."""
    rows = _read_csv("catalogue.csv")
    rows = [r for r in rows if r["in_stock"] == "True"]
    if categories:
        rows = [r for r in rows if r["category"] in categories]
    if max_price:
        rows = [r for r in rows if float(r["price_inr"]) <= max_price]
    return rows[:limit]


def ground_skus(sku_ids: list[str]) -> list[dict]:
    """Step [5]: verify chosen SKU ids against the catalogue; silently drops
    any id the model invented rather than trusting it."""
    catalogue = _read_csv("catalogue.csv")
    by_id = {r["sku_id"]: r for r in catalogue}
    grounded = []
    for sid in sku_ids:
        row = by_id.get(sid)
        if row is not None:
            grounded.append(row)
    return grounded


def list_customer_ids() -> list[dict]:
    """For the demo UI's customer picker."""
    return [{"customer_id": c["customer_id"], "city_tier": c["city_tier"]} for c in _read_csv("customers.csv")]


# ── Decision log ──────────────────────────────────────────────────────────
# Every pipeline run appends one line here: the audit trail the build note's
# cost line is computed from, not a hand-wavy estimate.
#
# Vercel's deployed filesystem is read-only outside /tmp (found the hard way —
# see docs/build-note.md "what broke"): writing to data/decision_log.jsonl
# there raises OSError. /tmp is writable but ephemeral per invocation, so on
# Vercel this is a best-effort log, not a durable one — a real limitation of
# this MVP, not something to paper over.
DECISION_LOG = (Path("/tmp") if os.environ.get("VERCEL") else DATA_DIR) / "decision_log.jsonl"


def append_decision(entry: dict) -> dict:
    """Never lets a logging failure break the caller's actual result."""
    entry = {**entry, "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        with DECISION_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return entry


def load_decisions() -> list[dict]:
    if not DECISION_LOG.exists():
        return []
    return [json.loads(line) for line in DECISION_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]
