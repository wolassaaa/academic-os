"""Reserved provider-neutral model contract. No API calls and no credential access."""
from typing import Protocol


class ModelGateway(Protocol):
    def analyze(self, request: dict) -> dict: ...


class DisabledModelGateway:
    def analyze(self, request: dict) -> dict:
        return {"schema_version": 1, "request_id": request.get("request_id"), "status": "disabled",
                "facts": [], "suggestions": [], "warnings": ["model_analysis_disabled"],
                "error": None, "usage": None}
