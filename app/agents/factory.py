"""Expose the active NexusOps Agent framework metadata."""
from app.core.config import Settings


def agent_framework_status(settings: Settings) -> dict:
    active = "event_driven_multi_agent"
    requested = (settings.agent_framework or active).lower()
    return {"requested": requested, "active": active,
            "available": [active],
            "fallback": requested not in {active, "multi_agent", "actors"}}
