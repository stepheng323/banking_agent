"""User-state rendering for planner context summaries."""

from apps.chat.src.agent.orchestrator.workflows.planner.context.context_rendering_core import (
    CONTEXT_USER_STATE_MAX_CHARS,
    _clip_text,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_types import TurnContextSummary


def build_user_state_summary_from_summary(summary: TurnContextSummary) -> str | None:
    if not any(
        [
            summary.profile_name,
            summary.account_lines,
            summary.beneficiary_lines,
            summary.history_lines,
        ]
    ):
        return None

    parts = ["User State:"]

    if summary.profile_name:
        parts.append(f"- Name: {summary.profile_name}")

    if summary.account_lines:
        parts.append("- Accounts:")
        for line in summary.account_lines:
            parts.append(f"  • {line}")
        if summary.remaining_accounts > 0:
            parts.append(f"  • +{summary.remaining_accounts} more account(s)")

    if summary.beneficiary_lines:
        parts.append(f"- Beneficiaries: {len(summary.beneficiary_lines) + summary.remaining_beneficiaries} saved")
        parts.append(f"  • Preview: {', '.join(summary.beneficiary_lines)}")
        if summary.remaining_beneficiaries > 0:
            parts.append(f"  • +{summary.remaining_beneficiaries} more")

    if summary.history_lines:
        parts.append("\nRecent Chat:")
        for line in summary.history_lines:
            parts.append(f"- {line}")

    return _clip_text("\n".join(parts), CONTEXT_USER_STATE_MAX_CHARS)


__all__ = ["build_user_state_summary_from_summary"]
