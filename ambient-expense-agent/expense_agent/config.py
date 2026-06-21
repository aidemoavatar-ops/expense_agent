import os
from dataclasses import dataclass

if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "False").upper() == "TRUE":
    import google.auth

    _, project_id = google.auth.default()
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")


@dataclass
class Config:
    model: str = "gemini-3.1-flash-lite"
    auto_approve_threshold: float = 100.0


config = Config()
