"""Capability contracts and limitations handling for graceful degradation.

This module enables the banking agent to:
1. Know its own limitations explicitly
2. Detect when users request unsupported features
3. Respond gracefully with alternatives

Principle: The system never "fails" - it negotiates down to what it can do.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TimeScope(str, Enum):
    """Supported time scopes for queries."""

    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"
    LAST_3_MONTHS = "last_3_months"
    LAST_6_MONTHS = "last_6_months"
    LAST_YEAR = "last_year"
    ALL_TIME = "all_time"
    CUSTOM_RANGE = "custom_range"


@dataclass
class CapabilityContract:
    """
    Declares what a graph/service can and cannot do.

    Each graph should define its own contract so the system
    can check capabilities before execution.
    """

    # Time-based capabilities
    supported_time_scopes: list[TimeScope] = field(
        default_factory=lambda: [
            TimeScope.LAST_7_DAYS,
            TimeScope.LAST_30_DAYS,
            TimeScope.LAST_6_MONTHS,
        ]
    )
    max_lookback_days: int = 180  # 6 months default
    supports_all_time: bool = False

    # Feature flags
    supports_export_pdf: bool = False
    supports_export_csv: bool = False
    supports_category_breakdown: bool = False
    supports_recipient_filter: bool = True
    supports_amount_filter: bool = False

    # Limits
    max_results: int = 50
    max_recipients_per_query: int = 1

    def check_time_scope(self, requested: TimeScope) -> tuple[bool, TimeScope | None]:
        """
        Check if a time scope is supported.

        Returns:
            (is_supported, best_alternative)
        """
        if requested in self.supported_time_scopes:
            return True, None

        # Find best alternative (closest to requested)
        scope_order = list(TimeScope)
        requested_idx = scope_order.index(requested)

        # Find closest supported scope
        for offset in range(1, len(scope_order)):
            # Try smaller scope first (more likely available)
            if requested_idx - offset >= 0:
                candidate = scope_order[requested_idx - offset]
                if candidate in self.supported_time_scopes:
                    return False, candidate

            # Then try larger scope
            if requested_idx + offset < len(scope_order):
                candidate = scope_order[requested_idx + offset]
                if candidate in self.supported_time_scopes:
                    return False, candidate

        return False, self.supported_time_scopes[0] if self.supported_time_scopes else None


@dataclass
class LimitationContext:
    """Context for generating limitation responses."""

    requested_feature: str
    reason: str | None = None
    supported_alternatives: list[str] = field(default_factory=list)
    next_actions: list[dict[str, str]] = field(default_factory=list)
    best_alternative: str | None = None


class LimitationsHandler:
    """
    Generates consistent, helpful responses for unsupported requests.

    Pattern:
    1. Acknowledge the request
    2. State current limit (without saying "I can't")
    3. Offer the closest alternative
    4. Suggest next actions
    """

    @staticmethod
    def generate_response(ctx: LimitationContext) -> str:
        """Generate a graceful limitation response."""
        lines = []

        # Acknowledge
        lines.append(f"Got it — *{ctx.requested_feature}*.")
        lines.append("")

        # State limit with "yet" to reduce frustration
        if ctx.reason:
            lines.append(ctx.reason)
        else:
            lines.append(f"*{ctx.requested_feature}* isn't available yet.")

        # Offer alternative
        if ctx.best_alternative:
            lines.append("")
            lines.append(f"I can show you *{ctx.best_alternative}* instead.")

        # Next actions
        if ctx.next_actions:
            lines.append("")
            lines.append("Reply:")
            for action in ctx.next_actions:
                lines.append(f"• `{action['command']}` — {action['description']}")

        return "\n".join(lines)

    @classmethod
    def time_scope_limitation(
        cls,
        requested: TimeScope,
        best_supported: TimeScope,
        max_days: int,
    ) -> str:
        """Generate response for unsupported time scope."""
        time_labels = {
            TimeScope.LAST_7_DAYS: "last 7 days",
            TimeScope.LAST_30_DAYS: "last 30 days",
            TimeScope.LAST_3_MONTHS: "last 3 months",
            TimeScope.LAST_6_MONTHS: "last 6 months",
            TimeScope.LAST_YEAR: "last year",
            TimeScope.ALL_TIME: "all time",
        }

        requested_label = time_labels.get(requested, str(requested.value))
        best_label = time_labels.get(best_supported, str(best_supported.value))

        ctx = LimitationContext(
            requested_feature=requested_label,
            reason=f"I can calculate totals up to *{max_days} days* at the moment.",
            best_alternative=best_label,
            next_actions=[
                {"command": "yes", "description": f"Show {best_label}"},
                {"command": "monthly", "description": "Month-by-month breakdown"},
            ],
        )
        return cls.generate_response(ctx)

    @classmethod
    def feature_not_available(
        cls,
        feature: str,
        alternatives: list[str] | None = None,
    ) -> str:
        """Generate response for unavailable feature."""
        ctx = LimitationContext(
            requested_feature=feature,
            reason=f"*{feature.capitalize()}* isn't available yet.",
            supported_alternatives=alternatives or [],
            best_alternative=alternatives[0] if alternatives else None,
        )
        return cls.generate_response(ctx)

    @classmethod
    def schedule_not_supported(cls, requested_when: str | None = None) -> str:
        """Generate response for scheduling request."""
        when_text = f"*{requested_when}*" if requested_when else "a scheduled time"
        ctx = LimitationContext(
            requested_feature=when_text,
            reason="I can only do immediate transactions right now.",
            next_actions=[
                {"command": "yes", "description": "Proceed now"},
                {"command": "remind", "description": "I'll remind you later"},
            ],
        )
        return cls.generate_response(ctx)

    @classmethod
    def international_not_supported(cls) -> str:
        """Generate response for international transfer request."""
        ctx = LimitationContext(
            requested_feature="international transfer",
            reason="I can only do transfers to *Nigerian banks* at the moment.",
            next_actions=[
                {"command": "local", "description": "Do a local transfer instead"},
            ],
        )
        return cls.generate_response(ctx)

    @classmethod
    def export_not_supported(cls, requested_format: str | None = None) -> str:
        """Generate response for export request."""
        format_text = f"{requested_format.upper()} export" if requested_format else "Export"
        ctx = LimitationContext(
            requested_feature=format_text,
            reason=f"*{format_text}* isn't available yet.",
            best_alternative="I can show the breakdown here",
            next_actions=[
                {"command": "show", "description": "Display here"},
            ],
        )
        return cls.generate_response(ctx)


def check_capabilities(
    requested: "RequestedScope",
    capabilities: CapabilityContract,
) -> tuple[bool, str | None]:
    """
    Check if requested scope is supported by capabilities.

    Returns:
        (is_supported, limitation_message)
        If supported, limitation_message is None.
        If not supported, limitation_message contains the graceful response.
    """
    # Check time scope
    if requested.time_range:
        supported, best_alternative = capabilities.check_time_scope(requested.time_range)
        if not supported:
            return False, LimitationsHandler.time_scope_limitation(
                requested=requested.time_range,
                best_supported=best_alternative or TimeScope.LAST_6_MONTHS,
                max_days=capabilities.max_lookback_days,
            )

    # Check export request
    if requested.wants_export:
        if requested.export_format == "pdf" and not capabilities.supports_export_pdf:
            return False, LimitationsHandler.export_not_supported("pdf")
        if requested.export_format == "csv" and not capabilities.supports_export_csv:
            return False, LimitationsHandler.export_not_supported("csv")
        if not capabilities.supports_export_pdf and not capabilities.supports_export_csv:
            return False, LimitationsHandler.export_not_supported(requested.export_format)

    # Check scheduling request
    if requested.wants_schedule:
        return False, LimitationsHandler.schedule_not_supported(requested.schedule_frequency)

    # Check international request
    if requested.wants_international:
        return False, LimitationsHandler.international_not_supported()

    # All checks passed
    return True, None


# Pre-defined capability contracts for each graph
QUERY_CAPABILITIES = CapabilityContract(
    supported_time_scopes=[
        TimeScope.LAST_7_DAYS,
        TimeScope.LAST_30_DAYS,
        TimeScope.LAST_3_MONTHS,
        TimeScope.LAST_6_MONTHS,
    ],
    max_lookback_days=180,
    supports_all_time=False,
    supports_category_breakdown=False,
    supports_recipient_filter=True,
)

TRANSFER_CAPABILITIES = CapabilityContract(
    supported_time_scopes=[TimeScope.LAST_30_DAYS],
    max_lookback_days=30,
    supports_all_time=False,
)

ACCOUNT_MANAGEMENT_CAPABILITIES = CapabilityContract(
    supported_time_scopes=[TimeScope.LAST_6_MONTHS],
    max_lookback_days=180,
    supports_all_time=False,
)


@dataclass
class RequestedScope:
    """
    Extracted scope from user request.

    This is populated by the entity extractor to capture what the user
    is asking for beyond the basic intent/entities.
    """

    # Time-related
    time_range: TimeScope | None = None
    time_confidence: float = 0.0

    # Feature requests
    wants_export: bool = False
    export_format: str | None = None  # pdf, csv
    wants_schedule: bool = False
    schedule_frequency: str | None = None  # daily, weekly, monthly
    schedule_date: str | None = None  # "tomorrow", "Friday", etc.

    # Amount constraints
    requested_amount: float | None = None
    amount_exceeds_limit: bool = False

    # Entity constraints
    wants_international: bool = False
    wants_multi_recipient: bool = False
    recipient_count: int = 1

    def has_unsupported_request(self, capabilities: CapabilityContract) -> bool:
        """Check if any requested feature is unsupported."""
        # Check time scope
        if self.time_range:
            supported, _ = capabilities.check_time_scope(self.time_range)
            if not supported:
                return True

        # Check features
        if self.wants_export and not (capabilities.supports_export_pdf or capabilities.supports_export_csv):
            return True

        if self.wants_schedule:
            return True  # Not supported anywhere yet

        if self.wants_international:
            return True  # Not supported

        return False


def detect_time_scope(text: str) -> tuple[TimeScope | None, float]:
    """
    Detect time scope from user message text.

    Returns:
        (detected_scope, confidence)
    """
    text_lower = text.lower()

    # All time patterns
    all_time_patterns = [
        "all time", "all-time", "ever", "total ever", "since beginning",
        "since i started", "all transactions", "everything", "lifetime",
    ]
    for pattern in all_time_patterns:
        if pattern in text_lower:
            return TimeScope.ALL_TIME, 0.9

    # Specific time patterns
    time_patterns = [
        (["last week", "past week", "this week", "7 days", "last 7"], TimeScope.LAST_7_DAYS),
        (["last month", "past month", "this month", "30 days", "last 30"], TimeScope.LAST_30_DAYS),
        (["last 3 months", "past 3 months", "three months", "90 days"], TimeScope.LAST_3_MONTHS),
        (["last 6 months", "past 6 months", "six months", "180 days", "half year"], TimeScope.LAST_6_MONTHS),
        (["last year", "past year", "this year", "12 months", "one year"], TimeScope.LAST_YEAR),
    ]

    for patterns, scope in time_patterns:
        for pattern in patterns:
            if pattern in text_lower:
                return scope, 0.85

    return None, 0.0


def detect_feature_requests(text: str) -> dict[str, Any]:
    """
    Detect feature requests from user message.

    Returns dict with detected features.
    """
    text_lower = text.lower()
    features = {}

    # Export requests
    if any(word in text_lower for word in ["export", "download", "save as", "send to email"]):
        features["wants_export"] = True
        if "pdf" in text_lower:
            features["export_format"] = "pdf"
        elif "csv" in text_lower or "excel" in text_lower or "spreadsheet" in text_lower:
            features["export_format"] = "csv"

    # Schedule requests
    schedule_words = ["schedule", "later", "tomorrow", "next week", "on friday", "set up recurring", "automatically"]
    if any(word in text_lower for word in schedule_words):
        features["wants_schedule"] = True
        if "weekly" in text_lower:
            features["schedule_frequency"] = "weekly"
        elif "monthly" in text_lower:
            features["schedule_frequency"] = "monthly"
        elif "daily" in text_lower:
            features["schedule_frequency"] = "daily"

    # International
    if any(word in text_lower for word in ["international", "abroad", "foreign", "overseas", "usa", "uk", "ghana"]):
        features["wants_international"] = True

    return features


def extract_requested_scope(text: str) -> RequestedScope:
    """
    Extract full RequestedScope from user message.

    This should be called during entity extraction to capture
    scope-related requests.
    """
    time_scope, confidence = detect_time_scope(text)
    features = detect_feature_requests(text)

    return RequestedScope(
        time_range=time_scope,
        time_confidence=confidence,
        wants_export=features.get("wants_export", False),
        export_format=features.get("export_format"),
        wants_schedule=features.get("wants_schedule", False),
        schedule_frequency=features.get("schedule_frequency"),
        wants_international=features.get("wants_international", False),
    )

