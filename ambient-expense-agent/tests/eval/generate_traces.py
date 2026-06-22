"""
Trace generator for the expense approval agent evaluation.

Runs each dataset case through the ADK Runner, automatically handles
human-in-the-loop (HITL) approval pauses, and serializes traces to
artifacts/traces/generated_traces.json for grading with agents-cli.

Usage (from ambient-expense-agent/):
    uv run python tests/eval/generate_traces.py
"""

import json
import sys
from pathlib import Path

from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.workflow.utils._workflow_hitl_utils import create_request_input_response
from google.genai import types

from app.agent import root_agent

DATASET_PATH = Path("tests/eval/datasets/basic-dataset.json")
OUTPUT_PATH = Path("artifacts/traces/generated_traces.json")

# Automated HITL decisions injected for each case that pauses for human input.
# Clean high-value expenses get "approve"; injection attacks always get "reject".
_HITL_DECISIONS: dict[str, str] = {
    "manual_review_approve": "approve",
    "manual_review_reject": "reject",
    "injection_attempt": "reject",
}


def _hitl_id(events: list) -> str | None:
    """Return the interrupt id if any event signals a HITL pause, else None.

    ADK encodes RequestInput as a function_call with name="adk_request_input"
    and id=<interrupt_id> (e.g. "human_decision" or "security_decision").
    """
    for event in events:
        if not (event.content and event.content.parts):
            continue
        for part in event.content.parts:
            if not part.function_call:
                continue
            if part.function_call.name == "adk_request_input":
                return part.function_call.id or ""
    return None


def _part_to_dict(part: types.Part) -> dict | None:
    if part.text:
        return {"text": part.text}
    if part.function_call:
        fc = part.function_call
        try:
            args = json.loads(json.dumps(dict(fc.args or {})))
        except Exception:
            args = {}
        return {
            "functionCall": {
                "name": getattr(fc, "name", "") or "",
                "args": args,
                "id": getattr(fc, "id", "") or "",
            }
        }
    return None


def _build_turns(run_groups: list[tuple[str, list]]) -> list[dict]:
    """Convert (human_text, events) pairs into agent_data turns."""
    turns = []
    for turn_idx, (human_text, events) in enumerate(run_groups):
        turn_events: list[dict] = [
            {
                "author": "user",
                "content": {"role": "user", "parts": [{"text": human_text}]},
            }
        ]
        for event in events:
            actions = getattr(event, "actions", None)
            route = getattr(actions, "route", None) if actions else None

            if event.output is not None:
                if isinstance(event.output, (dict, list)):
                    out_repr = json.dumps(event.output)
                else:
                    out_repr = str(event.output)
                if len(out_repr) > 300:
                    out_repr = out_repr[:300] + "..."
                label = f"[NODE OUTPUT{' -> ' + route if route else ''}]"
                turn_events.append(
                    {
                        "author": "expense_approval",
                        "content": {
                            "role": "model",
                            "parts": [{"text": f"{label} {out_repr}"}],
                        },
                    }
                )

            if not (event.content and event.content.parts):
                continue
            parts = [d for pt in event.content.parts if (d := _part_to_dict(pt))]
            if not parts:
                continue
            author = getattr(event, "author", None) or "expense_approval"
            role = event.content.role or "model"
            turn_events.append(
                {"author": author, "content": {"role": role, "parts": parts}}
            )

        turns.append({"turn_index": turn_idx, "events": turn_events})
    return turns


def _last_model_text(run_groups: list[tuple[str, list]]) -> str:
    """Return the last non-empty text emitted by a model-role event."""
    last = ""
    for _human_text, events in run_groups:
        for event in events:
            if not (event.content and event.content.parts):
                continue
            if (event.content.role or "").lower() not in ("model", ""):
                continue
            for part in event.content.parts:
                if part.text:
                    last = part.text
    return last


def run_case(case_id: str, prompt_text: str) -> dict:
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="eval", app_name="eval")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="eval")
    run_cfg = RunConfig(streaming_mode=StreamingMode.SSE)

    initial_msg = types.Content(
        role="user", parts=[types.Part.from_text(text=prompt_text)]
    )
    events1 = list(
        runner.run(
            user_id="eval",
            session_id=session.id,
            new_message=initial_msg,
            run_config=run_cfg,
        )
    )
    run_groups: list[tuple[str, list]] = [(prompt_text, events1)]

    interrupt_id = _hitl_id(events1)
    if interrupt_id:
        decision = _HITL_DECISIONS.get(case_id, "reject")
        print(f"  HITL pause ({interrupt_id}); auto-decision: {decision!r}")
        # Resume requires a function_response matching the interrupt_id,
        # not plain text — the ADK runner checks for function_response parts
        # to identify resume messages and routes accordingly.
        resume_part = create_request_input_response(
            interrupt_id=interrupt_id, response={"result": decision}
        )
        resume_msg = types.Content(role="user", parts=[resume_part])
        events2 = list(
            runner.run(
                user_id="eval",
                session_id=session.id,
                new_message=resume_msg,
                run_config=run_cfg,
            )
        )
        run_groups.append((decision, events2))

    final_text = _last_model_text(run_groups)
    return {
        "eval_case_id": case_id,
        "prompt": {"role": "user", "parts": [{"text": prompt_text}]},
        "responses": [{"response": {"role": "model", "parts": [{"text": final_text}]}}],
        "agent_data": {
            "agents": {
                "expense_approval": {
                    "agent_id": "expense_approval",
                    "instruction": (
                        "Ambient expense approval: auto-approve under $100, "
                        "LLM risk review + HITL for amounts at or above $100, "
                        "security escalation for detected prompt-injection."
                    ),
                }
            },
            "turns": _build_turns(run_groups),
        },
    }


def main() -> None:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    traces: list[dict] = []

    for case in dataset["eval_cases"]:
        case_id: str = case["eval_case_id"]
        prompt_text: str = case["prompt"]["parts"][0]["text"]
        print(f"Running {case_id}...")
        try:
            trace = run_case(case_id, prompt_text)
            traces.append(trace)
            n = len(trace["agent_data"]["turns"])
            print(f"  OK ({n} turn{'s' if n != 1 else ''})")
        except Exception as exc:
            print(f"  FAILED: {exc}", file=sys.stderr)
            raise

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps({"eval_cases": traces}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nWrote {len(traces)} trace(s) -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
