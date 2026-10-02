"""Review decisions: validation rules and the /api/review endpoint, with the
decision log stubbed out so nothing is written to disk."""
import pytest
from fastapi.testclient import TestClient

from app import app
from src import review
from src.review import ReviewError, ReviewRequest, submit_review

DRAFT = "Diwali ke liye kurti dhundh rahe hain? COD available hai."


@pytest.fixture
def logged(monkeypatch):
    entries = []

    def fake_append(entry):
        entries.append(entry)
        return {**entry, "logged_at": "2026-10-02T10:00:00+00:00"}

    monkeypatch.setattr(review.data_access, "append_decision", fake_append)
    return entries


def _req(**overrides):
    base = dict(customer_id="CUST0031", action="approve", final_message=DRAFT, original_message=DRAFT, note="")
    base.update(overrides)
    return ReviewRequest(**base)


def test_approve_unchanged_draft_is_not_marked_edited(logged):
    entry = submit_review(_req())
    assert entry["action"] == "approve"
    assert entry["edited"] is False
    assert logged[0]["final_message"] == DRAFT


def test_approve_edited_draft_records_the_edit(logged):
    entry = submit_review(_req(final_message=DRAFT + " Size chart zaroor dekhein."))
    assert entry["edited"] is True
    assert entry["final_message"].endswith("zaroor dekhein.")


def test_approve_empty_message_is_refused(logged):
    with pytest.raises(ReviewError):
        submit_review(_req(final_message="   "))
    assert logged == []


def test_edit_that_promises_something_blocked_is_refused(logged):
    with pytest.raises(ReviewError, match="guaranteed"):
        submit_review(_req(final_message="Guaranteed perfect fit, order now!"))
    assert logged == []


def test_reject_needs_a_reason(logged):
    with pytest.raises(ReviewError):
        submit_review(_req(action="reject", note=""))
    entry = submit_review(_req(action="reject", note="Fit issues unresolved — CX to call first"))
    assert entry["action"] == "reject"
    assert entry["final_message"] is None


def test_unknown_customer_is_refused(logged):
    with pytest.raises(ReviewError):
        submit_review(_req(customer_id="CUST9999"))


def test_endpoint_round_trip(logged):
    client = TestClient(app)
    ok = client.post("/api/review", json={
        "customer_id": "CUST0031", "action": "approve",
        "final_message": DRAFT, "original_message": DRAFT,
    })
    assert ok.status_code == 200
    assert ok.json()["decision"]["customer_id"] == "CUST0031"

    blocked = client.post("/api/review", json={
        "customer_id": "CUST0031", "action": "approve", "final_message": "Lifetime free gift!",
    })
    assert blocked.status_code == 422
    assert "reword" in blocked.json()["detail"].lower()
