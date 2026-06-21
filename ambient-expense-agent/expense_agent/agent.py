# ruff: noqa
import base64
import json

from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.apps import App, ResumabilityConfig
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.workflow import Edge, FunctionNode, START, Workflow
from google.genai import types
from pydantic import BaseModel

from .config import config


# ── Schemas ──────────────────────────────────────────────────────────────────


class RawEvent(BaseModel):
    """Envelope that Pub/Sub (base64 data) and local tests (plain JSON) both satisfy."""

    data: str


class ExpenseReport(BaseModel):
    amount: float
    submitter: str
    category: str
    description: str
    date: str


class RiskAssessment(BaseModel):
    risk_level: str  # "low" | "medium" | "high"
    risk_factors: list[str]
    recommendation: str
    summary: str


# ── Node 1 · parse_expense ────────────────────────────────────────────────────


def parse_expense(node_input: RawEvent) -> ExpenseReport:
    """Decode base64 or plain-JSON payload and return a typed ExpenseReport."""
    raw = node_input.data
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
    except Exception:
        decoded = raw
    return ExpenseReport(**json.loads(decoded))


# ── Node 2 · route_expense ───────────────────────────────────────────────────


def route_expense(node_input: ExpenseReport) -> Event:
    """Branch on the dollar threshold; stash the expense in state for later nodes."""
    route = (
        "auto_approve"
        if node_input.amount < config.auto_approve_threshold
        else "llm_review"
    )
    return Event(
        output=node_input,
        route=route,
        state={"expense": node_input.model_dump()},
    )


# ── Node 3a · auto_approve ───────────────────────────────────────────────────


def auto_approve(node_input: ExpenseReport):
    """Instantly approve without involving the LLM or a human."""
    msg = (
        f"AUTO-APPROVED  ${node_input.amount:.2f}"
        f" · {node_input.submitter}"
        f" · {node_input.category}"
        f" (under ${config.auto_approve_threshold:.0f} threshold)"
    )
    yield Event(
        content=types.Content(role="model", parts=[types.Part.from_text(text=msg)])
    )
    yield Event(output="auto_approved")


# ── Node 3b · risk_reviewer (LLM) ────────────────────────────────────────────


risk_reviewer = LlmAgent(
    name="risk_reviewer",
    model=config.model,
    instruction=(
        "You are an expense compliance reviewer. "
        "Analyze the expense report provided and identify risk factors such as: "
        "unusual amounts for the category, vague or suspicious descriptions, "
        "missing information, or policy violations. "
        "Set risk_level to 'low', 'medium', or 'high'. "
        "Be concise and factual."
    ),
    output_schema=RiskAssessment,
    output_key="risk_assessment",
)


# ── Node 4 · request_human_approval (HITL) ───────────────────────────────────
#
# rerun_on_resume defaults to False for FunctionNode:
# the workflow pauses at the RequestInput, and when the human replies
# their text becomes this node's output — the function body does not rerun.


async def request_human_approval(
    ctx: Context, node_input: RiskAssessment, expense: dict
):
    """Surface the risk assessment and pause until a human decides."""
    exp = ExpenseReport(**expense)
    bullets = (
        "\n".join(f"  • {f}" for f in node_input.risk_factors) or "  (none identified)"
    )
    message = (
        f"══════════ EXPENSE APPROVAL REQUIRED ══════════\n"
        f"Submitter   : {exp.submitter}\n"
        f"Amount      : ${exp.amount:.2f}\n"
        f"Category    : {exp.category}\n"
        f"Date        : {exp.date}\n"
        f"Description : {exp.description}\n\n"
        f"Risk Level  : {node_input.risk_level.upper()}\n"
        f"Risk Factors:\n{bullets}\n"
        f"Recommendation: {node_input.recommendation}\n"
        f"Summary     : {node_input.summary}\n\n"
        f"Reply 'approve' or 'reject':"
    )
    yield RequestInput(interrupt_id="human_decision", message=message)


# ── Node 5 · record_outcome ───────────────────────────────────────────────────
#
# Receives:
#   auto path  → node_input == "auto_approved"
#   human path → node_input == whatever the human typed ("approve" / "reject")
# Both paths share the `expense` dict written to state by route_expense.


def record_outcome(ctx: Context, node_input: str, expense: dict):
    """Record the final decision and emit a visible summary."""
    exp = ExpenseReport(**expense)
    raw = node_input.strip().lower()

    if raw == "auto_approved":
        verdict, method = "approved", "auto"
    elif raw in ("approve", "approved", "yes", "y"):
        verdict, method = "approved", "human"
    else:
        verdict, method = "rejected", "human"

    result = {
        "decision": verdict,
        "method": method,
        "submitter": exp.submitter,
        "amount": exp.amount,
        "category": exp.category,
        "date": exp.date,
        "description": exp.description,
    }
    summary = (
        f"[{method.upper()}] {verdict.upper()}"
        f" · ${exp.amount:.2f} by {exp.submitter} ({exp.category})"
    )
    yield Event(
        content=types.Content(role="model", parts=[types.Part.from_text(text=summary)])
    )
    yield Event(output=json.dumps(result))


# ── Wrap plain functions as FunctionNodes ─────────────────────────────────────

parse_expense_node = FunctionNode(func=parse_expense)
route_expense_node = FunctionNode(func=route_expense)
auto_approve_node = FunctionNode(func=auto_approve)
request_human_approval_node = FunctionNode(func=request_human_approval)
record_outcome_node = FunctionNode(func=record_outcome)

# ── Graph ─────────────────────────────────────────────────────────────────────

root_agent = Workflow(
    name="expense_approval",
    description=(
        "Ambient expense approval: auto-approve under the threshold, "
        "LLM risk review + human-in-the-loop for amounts at or above it."
    ),
    input_schema=RawEvent,
    edges=[
        # ① ingest
        Edge(from_node=START, to_node=parse_expense_node),
        Edge(from_node=parse_expense_node, to_node=route_expense_node),
        # ② fast path — no LLM, no human
        Edge(
            from_node=route_expense_node,
            to_node=auto_approve_node,
            route="auto_approve",
        ),
        # ③ slow path — LLM review, then human gate
        Edge(from_node=route_expense_node, to_node=risk_reviewer, route="llm_review"),
        Edge(from_node=risk_reviewer, to_node=request_human_approval_node),
        # ④ both paths converge here
        Edge(from_node=auto_approve_node, to_node=record_outcome_node),
        Edge(from_node=request_human_approval_node, to_node=record_outcome_node),
    ],
)

app = App(
    root_agent=root_agent,
    name="app",  # must match the agent directory name used by get_fast_api_app
    resumability_config=ResumabilityConfig(resumability="required"),
)
