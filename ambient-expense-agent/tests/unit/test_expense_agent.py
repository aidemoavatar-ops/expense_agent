# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import json
from unittest.mock import MagicMock

import pytest

from expense_agent.agent import (
    ExpenseReport,
    RawEvent,
    RiskAssessment,
    auto_approve,
    parse_expense,
    record_outcome,
    route_expense,
)
from expense_agent.config import config


# ── Helpers ───────────────────────────────────────────────────────────────────

SAMPLE_EXPENSE = {
    "amount": 42.50,
    "submitter": "alice",
    "category": "Meals",
    "description": "Team lunch",
    "date": "2026-06-21",
}

SAMPLE_EXPENSE_HIGH = {**SAMPLE_EXPENSE, "amount": 850.00}


def make_raw_event(expense: dict, encode_base64: bool = False) -> RawEvent:
    payload = json.dumps(expense)
    if encode_base64:
        payload = base64.b64encode(payload.encode()).decode()
    return RawEvent(data=payload)


def collect_events(gen):
    """Drain a (sync) generator and return list of emitted events."""
    return list(gen)


# ── parse_expense ─────────────────────────────────────────────────────────────


class TestParseExpense:
    def test_plain_json(self):
        raw = make_raw_event(SAMPLE_EXPENSE, encode_base64=False)
        result = parse_expense(raw)
        assert isinstance(result, ExpenseReport)
        assert result.amount == 42.50
        assert result.submitter == "alice"
        assert result.category == "Meals"
        assert result.description == "Team lunch"
        assert result.date == "2026-06-21"

    def test_base64_encoded(self):
        raw = make_raw_event(SAMPLE_EXPENSE, encode_base64=True)
        result = parse_expense(raw)
        assert result.amount == 42.50
        assert result.submitter == "alice"

    def test_plain_and_base64_produce_same_result(self):
        plain = parse_expense(make_raw_event(SAMPLE_EXPENSE, encode_base64=False))
        encoded = parse_expense(make_raw_event(SAMPLE_EXPENSE, encode_base64=True))
        assert plain == encoded

    def test_invalid_json_raises(self):
        raw = RawEvent(data="not-json-and-not-base64")
        with pytest.raises(Exception):
            parse_expense(raw)


# ── route_expense ─────────────────────────────────────────────────────────────


class TestRouteExpense:
    def test_below_threshold_routes_auto_approve(self):
        expense = ExpenseReport(**{**SAMPLE_EXPENSE, "amount": config.auto_approve_threshold - 0.01})
        event = route_expense(expense)
        assert event.actions.route == "auto_approve"

    def test_at_threshold_routes_llm_review(self):
        expense = ExpenseReport(**{**SAMPLE_EXPENSE, "amount": config.auto_approve_threshold})
        event = route_expense(expense)
        assert event.actions.route == "llm_review"

    def test_above_threshold_routes_llm_review(self):
        expense = ExpenseReport(**SAMPLE_EXPENSE_HIGH)
        event = route_expense(expense)
        assert event.actions.route == "llm_review"

    def test_expense_stored_in_state(self):
        expense = ExpenseReport(**SAMPLE_EXPENSE)
        event = route_expense(expense)
        stored = event.actions.state_delta["expense"]
        assert stored["amount"] == SAMPLE_EXPENSE["amount"]
        assert stored["submitter"] == SAMPLE_EXPENSE["submitter"]

    def test_output_is_expense_report(self):
        expense = ExpenseReport(**SAMPLE_EXPENSE)
        event = route_expense(expense)
        # Event.output holds the model instance; auto-serialised only at session-store time
        assert event.output.amount == SAMPLE_EXPENSE["amount"]


# ── auto_approve ──────────────────────────────────────────────────────────────


class TestAutoApprove:
    def test_emits_output_auto_approved(self):
        expense = ExpenseReport(**SAMPLE_EXPENSE)
        events = collect_events(auto_approve(expense))
        output_events = [e for e in events if e.output is not None]
        assert len(output_events) == 1
        assert output_events[0].output == "auto_approved"

    def test_emits_content_event_for_ui(self):
        expense = ExpenseReport(**SAMPLE_EXPENSE)
        events = collect_events(auto_approve(expense))
        content_events = [e for e in events if e.content is not None]
        assert len(content_events) == 1
        text = content_events[0].content.parts[0].text
        assert "AUTO-APPROVED" in text
        assert str(SAMPLE_EXPENSE["amount"]) in text or "42.5" in text

    def test_message_contains_submitter_and_category(self):
        expense = ExpenseReport(**SAMPLE_EXPENSE)
        events = collect_events(auto_approve(expense))
        text = next(e.content.parts[0].text for e in events if e.content)
        assert SAMPLE_EXPENSE["submitter"] in text
        assert SAMPLE_EXPENSE["category"] in text


# ── record_outcome ────────────────────────────────────────────────────────────


class TestRecordOutcome:
    def _make_ctx(self):
        ctx = MagicMock()
        ctx.state = {"expense": SAMPLE_EXPENSE}
        return ctx

    def _run(self, verdict: str):
        ctx = self._make_ctx()
        return collect_events(record_outcome(ctx, verdict, SAMPLE_EXPENSE))

    def _final_result(self, verdict: str) -> dict:
        events = self._run(verdict)
        output_events = [e for e in events if e.output is not None]
        return json.loads(output_events[-1].output)

    # auto path
    def test_auto_approved_verdict(self):
        result = self._final_result("auto_approved")
        assert result["decision"] == "approved"
        assert result["method"] == "auto"

    # human approve variants
    @pytest.mark.parametrize("verdict", ["approve", "approved", "yes", "Approve", "APPROVE"])
    def test_human_approve_variants(self, verdict):
        result = self._final_result(verdict)
        assert result["decision"] == "approved"
        assert result["method"] == "human"

    # human reject variants
    @pytest.mark.parametrize("verdict", ["reject", "rejected", "no", "Reject", "REJECT", "nope"])
    def test_human_reject_variants(self, verdict):
        result = self._final_result(verdict)
        assert result["decision"] == "rejected"
        assert result["method"] == "human"

    def test_result_contains_expense_fields(self):
        result = self._final_result("approve")
        assert result["submitter"] == SAMPLE_EXPENSE["submitter"]
        assert result["amount"] == SAMPLE_EXPENSE["amount"]
        assert result["category"] == SAMPLE_EXPENSE["category"]
        assert result["date"] == SAMPLE_EXPENSE["date"]
        assert result["description"] == SAMPLE_EXPENSE["description"]

    def test_emits_content_event(self):
        events = self._run("approve")
        content_events = [e for e in events if e.content is not None]
        assert len(content_events) == 1
        text = content_events[0].content.parts[0].text
        assert "approved" in text.lower()
        assert SAMPLE_EXPENSE["submitter"] in text

    def test_emits_exactly_two_events(self):
        events = self._run("approve")
        assert len(events) == 2  # one content, one output
