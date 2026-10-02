"""Human review decisions for cases the pipeline held back (needs_human_review).

A reviewer can approve the draft as-is, edit it and approve, or reject it with
a reason. Nothing is sent from here: the decision is recorded for whatever
sends approved messages downstream.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

from src import data_access
from src.pipeline import output_gate

MAX_MESSAGE_CHARS = 1000


class ReviewError(ValueError):
    """A decision that can't be recorded, with a message safe to show the reviewer."""


class ReviewRequest(BaseModel):
    customer_id: str = Field(min_length=1)
    action: Literal["approve", "reject"]
    final_message: Optional[str] = None
    original_message: Optional[str] = None
    note: str = ""


def submit_review(req: ReviewRequest) -> dict:
    known = {c["customer_id"] for c in data_access.list_customer_ids()}
    if req.customer_id not in known:
        raise ReviewError(f"Unknown customer {req.customer_id}.")

    final = (req.final_message or "").strip()
    original = (req.original_message or "").strip()
    note = req.note.strip()

    if req.action == "approve":
        if not final:
            raise ReviewError("Add a message before approving.")
        if len(final) > MAX_MESSAGE_CHARS:
            raise ReviewError(f"Keep the message under {MAX_MESSAGE_CHARS} characters.")
        hits = output_gate(final)
        if hits:
            raise ReviewError(
                "The message promises something we don't offer ("
                + ", ".join(hits)
                + "). Reword it and approve again."
            )
    elif not note:
        raise ReviewError("Add a short note on why you're rejecting this.")

    return data_access.append_decision(
        {
            "type": "human_review",
            "customer_id": req.customer_id,
            "action": req.action,
            "edited": req.action == "approve" and final != original,
            "final_message": final if req.action == "approve" else None,
            "note": note,
        }
    )
