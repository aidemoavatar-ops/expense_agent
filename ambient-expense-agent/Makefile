# ── Prerequisites ─────────────────────────────────────────────────────────────
# uv       : https://docs.astral.sh/uv/getting-started/installation/
# agents-cli: uv tool install google-agents-cli
#
# On Windows run from Git Bash (where 'nohup' and '&' work as expected).
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: install playground lint test test-expense

## Install project dependencies (wraps `uv sync`)
install:
	agents-cli install

## Launch the ADK playground in the background (logs → playground.log)
## NOTE: calls `adk web` directly to avoid a shell-glob bug in agents-cli playground
##       where `--allow_origins '*'` expands to directory contents on Windows/Bash.
playground:
	@echo "Starting ADK playground at http://127.0.0.1:8080 ..."
	nohup uv run adk web . --host 127.0.0.1 --port 8080 --reload_agents > playground.log 2>&1 &
	@echo "Server starting. Follow logs with:  tail -f playground.log"
	@echo "Open http://127.0.0.1:8080 once you see 'Application startup complete'."

## Run ruff, ty, and codespell quality checks
lint:
	agents-cli lint

## Run unit + integration tests (auto-approve path only, no LLM credentials needed)
test:
	uv run pytest tests/unit tests/integration -v

## Send a $150 test expense to the running playground and print the HITL prompt
## PYTHONUTF8=1 is needed on Windows so the agent's Unicode box chars print correctly
test-expense:
	PYTHONUTF8=1 uv run python scripts/test_expense.py
