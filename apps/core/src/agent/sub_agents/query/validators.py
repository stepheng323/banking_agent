"""Query validators for guardrails and audit."""

from datetime import datetime

from shared.utils.logging import get_logger

logger = get_logger(__name__)


ALLOWED_QUERY_TYPES = {
    "balance",
    "total_spent",
    "total_received",
    "transaction_list",
    "search",
    "top_recipient",
    "top_sender",
    "breakdown",
    "affordability",
}

MAX_DATE_RANGE_DAYS = 365
MAX_LIMIT = 100
MIN_AMOUNT_CHECK = 1.0
MAX_AMOUNT_CHECK = 100_000_000.0  # 100 million naira


class QueryValidator:
    """Validate query parameters before execution.

    Guardrails:
    - Only allow known query types
    - Enforce maximum date ranges
    - Validate amount bounds for affordability checks
    - Log all queries for audit trail
    """

    @staticmethod
    def validate(params: dict, phone_number: str) -> tuple[bool, str | None]:
        """Validate query parameters.

        Args:
            params: Parsed query parameters dict
            phone_number: User phone for audit logging

        Returns:
            Tuple of (is_valid, error_message)
        """
        query_type = params.get("query_type", "")

        # Guardrail 1: Only allow known query types
        if query_type not in ALLOWED_QUERY_TYPES:
            logger.warning(
                "query_type_rejected",
                phone=phone_number[:6],
                query_type=query_type,
            )
            return False, f"Unsupported query type: {query_type}"

        # Guardrail 2: Validate date range
        date_range = params.get("date_range")
        if date_range:
            validation_result = QueryValidator._validate_date_range(date_range)
            if not validation_result[0]:
                return validation_result

        # Guardrail 3: Validate limit
        limit = params.get("limit", 10)
        if limit > MAX_LIMIT:
            return False, f"Maximum limit is {MAX_LIMIT} results."

        # Guardrail 4: Validate affordability amount
        if query_type == "affordability":
            amount_check = params.get("amount_check")
            if amount_check is None:
                return False, "Please specify an amount to check."
            if amount_check < MIN_AMOUNT_CHECK:
                return False, "Amount must be at least ₦1."
            if amount_check > MAX_AMOUNT_CHECK:
                return False, "Amount exceeds maximum allowed check."

        logger.info(
            "query_validated",
            phone=phone_number[:6],
            query_type=query_type,
            has_date_range=date_range is not None,
            limit=limit,
        )

        return True, None

    @staticmethod
    def _validate_date_range(date_range: dict) -> tuple[bool, str | None]:
        """Validate date range bounds."""
        try:
            start_str = date_range.get("start")
            end_str = date_range.get("end")

            if not start_str or not end_str:
                return True, None  # Missing dates use defaults

            start = datetime.strptime(start_str, "%Y-%m-%d")
            end = datetime.strptime(end_str, "%Y-%m-%d")

            # Check range isn't in the future
            today = datetime.now()
            if start > today or end > today:
                return False, "Cannot query future dates."

            # Check range isn't too large
            delta = (end - start).days
            if delta > MAX_DATE_RANGE_DAYS:
                return False, f"Date range cannot exceed {MAX_DATE_RANGE_DAYS} days."

            # Check start is before end
            if start > end:
                return False, "Start date must be before end date."

            return True, None

        except ValueError as e:
            logger.error("date_range_parse_error", error=str(e))
            return False, "Invalid date format. Use YYYY-MM-DD."
