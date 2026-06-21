# Coding Agent Guide

## Prerequisites

Install the CLI (one-time):
```bash
uv tool install google-agents-cli
```

---

## Development Phases

### Phase 1: Understand Requirements
Before writing any code, understand the project's requirements, constraints, and success criteria.

### Phase 2: Build and Implement
Implement agent logic in `app/`. Use `agents-cli playground` for interactive testing. Iterate based on user feedback.

### Phase 3: The Evaluation Loop (Main Iteration Phase)
Start with 1-2 eval cases, run `agents-cli eval generate`, then `agents-cli eval grade`, iterate by making changes and rerunning both commands until satisfied. Expect 5-10+ iterations. Once you have a baseline, reach for `agents-cli eval compare` (regression diffs), `agents-cli eval analyze` (cluster failure modes), and `agents-cli eval optimize` (auto-tune prompts). See the **Evaluation Guide** for metrics, dataset schema, LLM-as-judge config, and common gotchas.

### Phase 4: Pre-Deployment Tests
Run `uv run pytest tests/unit tests/integration`. Fix issues until all tests pass.

### Phase 5: Deploy to Dev
**Requires explicit human approval.** Run `agents-cli deploy` only after user confirms. See the **Deployment Guide** for details.

### Phase 6: Production Deployment
Ask the user: Option A (simple single-project) or Option B (full CI/CD pipeline with `agents-cli infra cicd`).

## Project Structure

Agent code lives in `expense_agent/` (not `app/`). `app/agent.py` re-exports
from `expense_agent.agent` so the scaffold entry point still works.

```
expense_agent/
  __init__.py   # from . import agent as agent  ← explicit re-export required by ruff
  config.py     # Config dataclass: model, auto_approve_threshold
  agent.py      # Workflow graph + App
```

## ADK 2.0 Workflow — Gotchas

### Edge syntax
The cheatsheet 3-tuple `(source, target, "route")` is **not accepted** by the
ADK 2.0 Pydantic schema. Always use explicit objects:

```python
from google.adk.workflow import Edge, FunctionNode, START

my_node = FunctionNode(func=my_func)       # wrap every plain function
Edge(from_node=START, to_node=my_node)     # unconditional
Edge(from_node=a_node, to_node=b_node, route="my_route")  # conditional
```

`LlmAgent` is already a `BaseNode` and can be used in `Edge` directly.

### App name must match the directory
`get_fast_api_app(agents_dir=...)` derives the runner app name from the
**directory** that contains `agent.py`. Set `App(name="app")` — not
`"expense_approval_app"` or any other string — or the session service
will fail to locate sessions with a `SessionNotFoundError`.

### `Event.output` is not auto-serialized
`Event(output=my_pydantic_model)` stores the model instance as-is.
Serialization to dict happens only when the session store persists it.
In unit tests, access fields via attribute (`event.output.amount`), not
subscript (`event.output["amount"]`).

### Routing and state on Event
```python
Event(output=value, route="my_route", state={"key": val})
# Readable in tests as:
event.actions.route          # → "my_route"
event.actions.state_delta    # → {"key": val}
```

### HITL (RequestInput)
With the default `rerun_on_resume=False` on `FunctionNode`, yield one
`RequestInput` and return. The human's reply text becomes the node's
output automatically — the function body does **not** re-execute.

## Tests and Evals

### Integration tests: input format
When `input_schema=RawEvent` is set on the Workflow, every test message
must be a JSON string matching `RawEvent`. Use the auto-approve path
(amount < $100) in tests to avoid LLM credentials:

```python
expense = json.dumps({"amount": 42.50, "submitter": "alice",
                      "category": "Meals", "description": "...", "date": "..."})
message_text = json.dumps({"data": expense})
types.Content(role="user", parts=[types.Part.from_text(text=message_text)])
```

### Eval grading
The `custom_response_quality` LLM-as-judge metric requires the
**Vertex AI Agent Platform API** to be enabled on the GCP project.
The default metric (`agent_turn_count`) is a pure-Python function
and works without any API calls. To use the LLM grader:
```bash
agents-cli eval grade --metrics custom_response_quality
```

## Lint Rules

### `ty` false positives on ADK kwargs
`ty` cannot resolve keyword arguments on ADK Pydantic classes
(`Event.route`, `Event.state`, `ResumabilityConfig.resumability`).
Suppressed in `pyproject.toml` under `[tool.ty.rules]`:
```toml
unknown-argument = "ignore"
```

### `__init__.py` re-exports
Use `from . import agent as agent` (not `from . import agent`) to
satisfy ruff F401 for intentional re-exports.

## Development Commands

| Command | Purpose |
|---------|---------|
| `agents-cli playground` | Interactive local testing |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests |
| `agents-cli eval dataset synthesize` | Synthesize multi-turn eval scenarios for your agent |
| `agents-cli eval generate` | Run agent on eval dataset, produce traces |
| `agents-cli eval grade` | Run agent evaluations on the traces |
| `agents-cli eval compare` | Compare two grade-results files (regression check) |
| `agents-cli eval analyze` | Cluster failure modes from grade results |
| `agents-cli eval metric list` | List built-in metrics available in the SDK |
| `agents-cli eval optimize` | Auto-tune agent prompts using eval data |
| `agents-cli lint` | Check code quality |
| `agents-cli infra single-project` | Set up project infrastructure (Terraform) |
| `agents-cli deploy` | Deploy to dev |
| `agents-cli scaffold enhance` | Add deployment target or CI/CD to project |
| `agents-cli scaffold upgrade` | Upgrade project to latest version |

---

## Operational Guidelines for Coding Agents

- **Code preservation**: Only modify code directly targeted by the user's request. Preserve all surrounding code, config values (e.g., `model`), comments, and formatting.
- **NEVER change the model** unless explicitly asked.
- **Model 404 errors**: Fix `GOOGLE_CLOUD_LOCATION` (e.g., `global` instead of `us-east1`), not the model name.
- **ADK tool imports**: Import the tool instance, not the module: `from google.adk.tools.load_web_page import load_web_page`
- **Run Python with `uv`**: `uv run python script.py`. Run `agents-cli install` first.
- **Stop on repeated errors**: If the same error appears 3+ times, fix the root cause instead of retrying.
- **Terraform conflicts** (Error 409): Use `terraform import` instead of retrying creation.
