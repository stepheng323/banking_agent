"""Typed query surface and presentation contract facade."""

from apps.chat.src.agent.graphs.query.models import QueryResult
from apps.chat.src.agent.graphs.query.presentation import presentation_planner
from apps.chat.src.agent.graphs.query.presentation.selection_resolver import find_selection_payload
from apps.chat.src.agent.graphs.query.presentation.surface_builder import (
    apply_selection_payload_to_query,
    build_focus_referent,
    build_query_transfer_handoff_payload,
    build_surface_view,
)
from apps.chat.src.agent.graphs.query.utils.timezone import lagos_today
from apps.chat.src.agent.shared.query_contracts import PresentationPlan


def build_presentation_plan(
    result: QueryResult,
    *,
    locale: str = "en",
    current_page: int = 0,
    show_expanded: bool = False,
    has_more: bool = False,
) -> PresentationPlan | None:
    presentation_planner.lagos_today = lagos_today
    return presentation_planner.build_presentation_plan(
        result,
        locale=locale,
        current_page=current_page,
        show_expanded=show_expanded,
        has_more=has_more,
    )

__all__ = [
    "apply_selection_payload_to_query",
    "build_focus_referent",
    "build_presentation_plan",
    "build_query_transfer_handoff_payload",
    "build_surface_view",
    "find_selection_payload",
    "lagos_today",
]
