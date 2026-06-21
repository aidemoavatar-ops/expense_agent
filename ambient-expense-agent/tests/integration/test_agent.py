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

import json

from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent

# Valid expense event under $100 → auto-approve path (no LLM call needed).
_EXPENSE = json.dumps(
    {
        "amount": 42.50,
        "submitter": "alice",
        "category": "Meals",
        "description": "Team lunch",
        "date": "2026-06-21",
    }
)
_MESSAGE_TEXT = json.dumps({"data": _EXPENSE})


def test_agent_stream() -> None:
    """
    Integration test: auto-approve path runs end-to-end without an LLM call
    and emits at least one content event with text (the approval summary).
    """
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="test_user", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    message = types.Content(
        role="user", parts=[types.Part.from_text(text=_MESSAGE_TEXT)]
    )

    events = list(
        runner.run(
            new_message=message,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )
    assert len(events) > 0, "Expected at least one event"

    has_text_content = any(
        event.content
        and event.content.parts
        and any(part.text for part in event.content.parts)
        for event in events
    )
    assert has_text_content, "Expected at least one event with text content"

    # Verify the workflow completed with an auto-approved outcome.
    output_events = [e for e in events if e.output is not None]
    assert output_events, "Expected a final output event"
    result = json.loads(output_events[-1].output)
    assert result["decision"] == "approved"
    assert result["method"] == "auto"
    assert result["submitter"] == "alice"
