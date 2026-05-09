"""Query presentation subsystem."""

from .presentation_planner import build_presentation_plan
from .selection_resolver import find_selection_payload
from .surface_builder import (
    apply_selection_payload_to_query,
    build_focus_referent,
    build_query_transfer_handoff_payload,
    build_surface_view,
    build_surface_view_context,
    result_query_contract,
)

__all__ = [
    "apply_selection_payload_to_query",
    "build_focus_referent",
    "build_presentation_plan",
    "build_query_transfer_handoff_payload",
    "build_surface_view",
    "build_surface_view_context",
    "find_selection_payload",
    "result_query_contract",
]
