"""
Send a test expense to the running ADK playground and print the response.

Usage:
    uv run python scripts/test_expense.py

The $150 amount exceeds the $100 auto-approve threshold, so the workflow will:
  1. Parse and security-check the expense
  2. Run the risk_reviewer LLM agent
  3. Pause and surface the HITL prompt ("approve" / "reject")

Open http://localhost:8000 to complete the approval in the playground UI.
"""

import json
import sys
import urllib.error
import urllib.request

# Ensure UTF-8 output on Windows consoles that default to cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://127.0.0.1:8080"
APP_NAME = "app"
USER_ID = "demo_user"

EXPENSE = {
    "amount": 150.0,
    "submitter": "alice@company.com",
    "category": "software",
    "description": "IDE License",
    "date": "2026-06-06",
}


def post_json(url: str, payload: dict) -> dict | list:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        print(f"HTTP {exc.code} from {url}: {detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(
            f"Cannot reach {url}: {exc.reason}\n"
            "Is the playground running? Start it with: make playground",
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> None:
    # ── 1. Create a fresh session ──────────────────────────────────────────────
    session_url = f"{BASE_URL}/apps/{APP_NAME}/users/{USER_ID}/sessions"
    print(f"Creating session at {session_url} ...")
    session = post_json(session_url, {})
    session_id = session["id"]
    print(f"  Session: {session_id}\n")

    # ── 2. Build the RawEvent envelope (matches the Workflow's input_schema) ───
    # The agent expects: Content(text=json.dumps({"data": json.dumps(expense)}))
    msg_text = json.dumps({"data": json.dumps(EXPENSE)})

    # ── 3. POST /run ───────────────────────────────────────────────────────────
    print(f"Sending expense to POST {BASE_URL}/run :")
    print(json.dumps(EXPENSE, indent=2), "\n")

    events: list = post_json(
        f"{BASE_URL}/run",
        {
            "app_name": APP_NAME,
            "user_id": USER_ID,
            "session_id": session_id,
            "new_message": {
                "role": "user",
                "parts": [{"text": msg_text}],
            },
        },
    )

    # ── 4. Print events ────────────────────────────────────────────────────────
    hitl_interrupt_id = None

    print("-" * 62)
    print(f"Received {len(events)} event(s):\n")
    for i, ev in enumerate(events, 1):
        content = ev.get("content")
        output = ev.get("output")
        actions = ev.get("actions", {})
        route = actions.get("route")

        if content:
            parts = content.get("parts") or []
            for part in parts:
                if part.get("text"):
                    print(f"[{i}] CONTENT:\n{part['text']}\n")
                # ADK encodes RequestInput as a functionCall inside content.parts
                fc = part.get("functionCall")
                if fc and fc.get("id") in ("human_decision", "security_decision"):
                    hitl_interrupt_id = fc["id"]
                    msg = fc.get("args", {}).get("message", "")
                    print(f"[{i}] [HITL PAUSE] interrupt_id={hitl_interrupt_id}")
                    if msg:
                        print(msg)

        if output is not None:
            print(f"[{i}] OUTPUT: {output}")

        if route:
            print(f"[{i}] ROUTE -> {route}")

    print("-" * 62)
    print()
    if hitl_interrupt_id:
        print("The workflow is paused waiting for a human decision.")
        print(f"-> Open http://127.0.0.1:8080/dev-ui/?app=app in your browser.")
        print(f"-> Select user '{USER_ID}', session '{session_id}'.")
        print("-> Type 'approve' or 'reject' in the chat input and press Send.")
    else:
        print("No HITL pause detected - check the events above for the outcome.")


if __name__ == "__main__":
    main()
