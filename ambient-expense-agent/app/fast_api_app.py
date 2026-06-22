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
import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from google.adk.cli.fast_api import get_fast_api_app
from pydantic import BaseModel, ConfigDict, Field

from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback
from expense_agent.agent import RawEvent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_logger = logging.getLogger(__name__)

setup_telemetry()

_use_gcp = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "False").upper() == "TRUE"

if _use_gcp:
    import google.auth
    from google.cloud import logging as google_cloud_logging

    _, project_id = google.auth.default()
    _gcp_logger = google_cloud_logging.Client().logger(__name__)

    def _log_struct(data: dict[str, Any]) -> None:
        _gcp_logger.log_struct(data, severity="INFO")

else:
    _std_logger = logging.getLogger(__name__)

    def _log_struct(data: dict[str, Any]) -> None:
        _std_logger.info(data)


allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)

logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
session_service_uri = None
artifact_service_uri = f"gs://{logs_bucket_name}" if logs_bucket_name else None

app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=artifact_service_uri,
    allow_origins=allow_origins,
    session_service_uri=session_service_uri,
    otel_to_cloud=False,
)
app.title = "ambient-expense-agent"
app.description = "API for interacting with the Agent ambient-expense-agent"

# ── Pub/Sub push endpoint ─────────────────────────────────────────────────────


class _PubSubMsg(BaseModel):
    """Cloud Pub/Sub message envelope (push format)."""

    model_config = ConfigDict(populate_by_name=True)

    data: str = ""
    message_id: str = Field("", alias="messageId")
    publish_time: str = Field("", alias="publishTime")
    attributes: dict[str, str] = Field(default_factory=dict)


class PubSubPushBody(BaseModel):
    """Outer envelope sent by a Pub/Sub push subscription."""

    message: _PubSubMsg
    # Fully-qualified path: projects/<proj>/subscriptions/<name>
    subscription: str


@app.post("/pubsub")
async def pubsub_push(request: Request, body: PubSubPushBody) -> dict[str, Any]:
    """Accept a Pub/Sub push message and feed it into the expense workflow.

    Delegates to the ADK server's own /apps/.../sessions and /run endpoints via
    ASGI transport so sessions are stored in the ADK's session service and appear
    in the dev UI.  Returns HTTP 200 to acknowledge; HTTP 500 causes Pub/Sub retry.
    """
    # Normalize "projects/my-proj/subscriptions/my-sub" → "my-sub"
    subscription_short = body.subscription.split("/")[-1]
    message_id = body.message.message_id or "unknown"
    session_id = f"{subscription_short}-{message_id}"
    user_id = "pubsub"
    app_name = "app"

    _logger.info(
        "pubsub received subscription=%s message_id=%s session=%s",
        subscription_short,
        message_id,
        session_id,
    )

    raw_event = RawEvent(data=body.message.data)

    # Delegate to the ADK server's own session and run endpoints so that sessions
    # are stored in the ADK session service and appear in the dev UI.
    # Real HTTP to localhost (loopback) avoids SQLAlchemy StaticPool re-entrancy
    # that breaks when using ASGI transport within the same coroutine.
    base_url = str(request.base_url).rstrip("/")
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        # Create the session (409 means it already exists — idempotent).
        sess_resp = await client.post(
            f"/apps/{app_name}/users/{user_id}/sessions/{session_id}", json={}
        )
        if sess_resp.status_code not in (200, 409):
            raise HTTPException(
                status_code=500,
                detail=f"session create failed: {sess_resp.status_code} {sess_resp.text}",
            )

        # Run the workflow through the ADK /run endpoint.
        run_resp = await client.post(
            "/run",
            json={
                "app_name": app_name,
                "user_id": user_id,
                "session_id": session_id,
                "new_message": {
                    "role": "user",
                    "parts": [{"text": raw_event.model_dump_json()}],
                },
            },
        )
        if run_resp.status_code != 200:
            raise HTTPException(
                status_code=500,
                detail=f"run failed: {run_resp.status_code} {run_resp.text}",
            )
        events = run_resp.json()

    _logger.info("pubsub done session=%s event_count=%d", session_id, len(events))
    return {
        "status": "ok",
        "session_id": session_id,
        "event_count": len(events),
    }


@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect and log feedback."""
    _log_struct(feedback.model_dump())
    return {"status": "success"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
