"""Structured smart context for LLM extraction.

Provides a token-efficient, explicit context schema for injection into LLM prompts.
"""

from pydantic import BaseModel, Field


class SmartContext(BaseModel):
    """Structured context for LLM extraction (target: < 1000 tokens)."""

    active_flow: str | None = Field(default=None, description="Current flow: transfer, airtime, data, or None")
    flow_step: str | None = Field(default=None, description="Current step: amount, recipient, confirm, etc.")

    known_entities: dict = Field(default_factory=dict, description="Already extracted values for current flow")
    pending_transaction: dict = Field(default_factory=dict, description="Transaction details being constructed")
    recent_transactions: list[dict] = Field(
        default_factory=list,
        description="Last 3 successful transactions for 'same as before' references",
    )
    saved_beneficiaries: list[dict] = Field(
        default_factory=list,
        description="Top 5 most relevant saved beneficiaries",
    )

    user_language: str = Field(default="en", description="User's preferred language")
    previous_system_message: str = Field(default="", description="Last assistant response (truncated)")

    MAX_RECENT_TRANSACTIONS = 3
    MAX_BENEFICIARIES = 5
    MAX_PREVIOUS_MESSAGE_CHARS = 150
    DEFAULT_TOKEN_BUDGET = 500
    CHARS_PER_TOKEN = 4  # Rough approximation

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count from text (rough approximation)."""
        return len(text) // self.CHARS_PER_TOKEN + 1

    def to_compact_string(self, max_tokens: int | None = None) -> str:
        """Format context for LLM injection (token-efficient).

        Args:
            max_tokens: Maximum token budget. If exceeded, truncates lower-priority fields.
        """
        if max_tokens is None:
            max_tokens = self.DEFAULT_TOKEN_BUDGET

        parts = []

        if self.active_flow:
            parts.append(f"Flow: {self.active_flow} ({self.flow_step or 'unknown step'})")

        if self.known_entities:
            entity_str = ", ".join(f"{k}={v}" for k, v in self.known_entities.items() if v is not None)
            if entity_str:
                parts.append(f"Known: {entity_str}")

        if self.pending_transaction:
            pending_str = ", ".join(f"{k}={v}" for k, v in self.pending_transaction.items() if v is not None)
            if pending_str:
                parts.append(f"Pending: {pending_str}")

        current = "\n".join(parts)
        remaining_tokens = max_tokens - self.estimate_tokens(current)

        if self.recent_transactions and remaining_tokens > 50:
            txs = []
            for tx in self.recent_transactions[: self.MAX_RECENT_TRANSACTIONS]:
                tx_type = tx.get("type", "transfer")
                amount = tx.get("amount", 0)
                recipient = tx.get("recipient_name") or tx.get("recipient_phone", "")
                txs.append(f"{tx_type}:₦{amount:,.0f}→{recipient}")
            if txs:
                tx_str = f"Recent: {'; '.join(txs)}"
                if self.estimate_tokens(tx_str) <= remaining_tokens:
                    parts.append(tx_str)
                    remaining_tokens -= self.estimate_tokens(tx_str)

        if self.saved_beneficiaries and remaining_tokens > 30:
            names = [b.get("name") or b.get("alias", "") for b in self.saved_beneficiaries[: self.MAX_BENEFICIARIES]]
            names = [n for n in names if n]
            if names:
                ben_str = f"Beneficiaries: {', '.join(names)}"
                if self.estimate_tokens(ben_str) <= remaining_tokens:
                    parts.append(ben_str)
                    remaining_tokens -= self.estimate_tokens(ben_str)

        if self.user_language and self.user_language != "en":
            parts.append(f"Language: {self.user_language}")

        if self.previous_system_message and remaining_tokens > 20:
            max_chars = min(self.MAX_PREVIOUS_MESSAGE_CHARS, remaining_tokens * self.CHARS_PER_TOKEN)
            truncated = self.previous_system_message[:max_chars]
            if len(self.previous_system_message) > max_chars:
                truncated += "..."
            parts.append(f"LastMsg: {truncated}")

        return "\n".join(parts) if parts else ""

    @classmethod
    def from_conversation_state(cls, conv_state: dict | None, user_context: dict | None = None) -> "SmartContext":
        """Build SmartContext from conversation state and user context."""
        if not conv_state:
            conv_state = {}
        if not user_context:
            user_context = {}

        return cls(
            active_flow=conv_state.get("active_flow"),
            flow_step=conv_state.get("flow_state"),
            known_entities={
                "amount": conv_state.get("amount"),
                "recipient_name": conv_state.get("recipient_name"),
                "recipient_account": conv_state.get("recipient_account"),
                "recipient_bank": conv_state.get("recipient_bank_name"),
                "recipient_phone": conv_state.get("recipient_phone"),
                "network": conv_state.get("network"),
            },
            pending_transaction={
                "amount": conv_state.get("amount"),
                "recipient": conv_state.get("recipient_name") or conv_state.get("recipient_phone"),
            },
            recent_transactions=user_context.get("recent_transactions", [])[:3],
            saved_beneficiaries=user_context.get("beneficiaries", [])[:5],
            user_language=user_context.get("detected_language", "en"),
            previous_system_message=user_context.get("last_response", ""),
        )
