"""Canonical event-plane constants shared by ingestion and retention."""
from .versions import BEHAVIOR_EVENT_SCHEMA_VERSION

SCHEMA_VERSION = BEHAVIOR_EVENT_SCHEMA_VERSION  # compatibility alias
CATEGORIES = {
    "observation", "assumption", "inference", "decision", "action",
    "tool_result", "validation", "correction", "contradiction", "retry",
    "state_transition", "handoff", "completion", "blocker",
}
