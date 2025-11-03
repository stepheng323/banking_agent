"""Intent parsing nodes for the transfer agent."""
import json
import re
import traceback
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.banking.transfer.prompts import INTENT_PARSER_PROMPT


class IntentNodes:
    """Nodes for intent parsing and initial processing."""

    def __init__(self, llm: Any) -> None:
        """Initialize with LLM instance."""
        self.llm = llm

    @staticmethod
    def _normalize_amount_value(value: Any) -> Optional[float]:
        """Normalize numeric amount values to float."""
        if value is None:
            return None
        try:
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                cleaned = value.strip().lower().replace(",", "")
                multiplier = 1.0
                if cleaned.endswith("k"):
                    cleaned = cleaned[:-1]
                    multiplier = 1000.0
                if cleaned.startswith("₦"):
                    cleaned = cleaned[1:]
                cleaned = cleaned.strip()
                if not cleaned:
                    return None
                return float(cleaned) * multiplier
        except (ValueError, TypeError):
            return None
        return None

    @staticmethod
    def _extract_recipients(parsed_intent: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Derive a list of recipients from parsed intent data."""
        explicit_recipients = parsed_intent.get("recipients") or []
        recipients: List[Dict[str, Any]] = []

        seen_names: set[str] = set()

        for recipient in explicit_recipients:
            if not isinstance(recipient, dict):
                continue
            cleaned = {k: v for k, v in recipient.items()
                       if v not in (None, "", [])}
            name = cleaned.get("name")
            if name:
                normalized = str(name).strip()
                if normalized:
                    cleaned["name"] = normalized
                    if normalized.lower() in seen_names:
                        continue
                    seen_names.add(normalized.lower())
            recipients.append(cleaned)

        primary_recipient = parsed_intent.get("recipient") or {}
        name = primary_recipient.get("name")

        if name:
            # Only split names when multiple recipients are implied
            name_segment = re.split(
                r"\bthen\b", str(name), flags=re.IGNORECASE)[0]
            if not recipients:
                split_candidates = re.split(
                    r"\s*(?:and|&|,|\+|\band\b|\bplus\b)\s*", name_segment, flags=re.IGNORECASE
                )
                split_names = [
                    candidate.strip()
                    for candidate in split_candidates
                    if candidate.strip()
                ]
                if len(split_names) > 1:
                    for candidate in split_names:
                        if candidate.lower() in seen_names:
                            continue
                        seen_names.add(candidate.lower())
                        recipients.append({"name": candidate})
                else:
                    recipients.append({k: v for k, v in primary_recipient.items()
                                       if v not in (None, "", [])})
            else:
                # Ensure the primary recipient is included when explicit list exists
                recipients.insert(
                    0, {k: v for k, v in primary_recipient.items()
                        if v not in (None, "", [])}
                )

        if not recipients and primary_recipient:
            recipients.append(
                {k: v for k, v in primary_recipient.items() if v not in (None, "", [])}
            )

        # Deduplicate while preserving order
        deduped: List[Dict[str, Any]] = []
        seen_names: set[str] = set()
        for recipient in recipients:
            name = str(recipient.get("name", "")).strip()
            if name and name.lower() in seen_names:
                continue
            if name:
                seen_names.add(name.lower())
            deduped.append(recipient)

        return deduped

    async def intent_parser_node(self, state: TransferState) -> TransferState:
        """Extract structured transfer information from user message."""
        print("🧠 INTENT PARSER: Analyzing user message...")

        user_message = state["message"]

        all_beneficiaries = state.get("all_beneficiaries", [])
        beneficiary_context = ""

        if all_beneficiaries:
            beneficiary_names = [
                f"- {b.get('nickname', b.get('name', 'Unknown'))} ({b.get('bank_name', 'Bank')})"
                for b in all_beneficiaries[:5]
            ]
            beneficiary_context = f"""Saved beneficiaries (match names even with typos):
{chr(10).join(beneficiary_names)}
"""

        prompt = INTENT_PARSER_PROMPT.format(
            user_message=user_message,
            beneficiary_context=beneficiary_context
        )
        messages = [HumanMessage(content=prompt)]

        try:
            response = await self.llm.ainvoke(messages)

            if hasattr(response, "content") and isinstance(response.content, str):
                response_text = response.content
            else:
                raise ValueError("LLM response has no valid content")

            if "```json" in response_text:
                response_text = response_text.split(
                    "```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split(
                    "```")[1].split("```")[0].strip()

            parsed_intent = json.loads(response_text)

            if "transfer_details" not in state or state.get("transfer_details") is None:
                state["transfer_details"] = {
                    "recipient": {"is_new_beneficiary": True},
                    "amount": {"currency": "NGN", "needs_calculation": False},
                    "source_account": {},
                    "purpose": None,
                    "notes": None,
                }

            if "recipient" not in state["transfer_details"] or state["transfer_details"]["recipient"] is None:
                state["transfer_details"]["recipient"] = {
                    "is_new_beneficiary": True}
            elif not state["transfer_details"]["recipient"]:
                state["transfer_details"]["recipient"] = {
                    "is_new_beneficiary": True}

            if "amount" not in state["transfer_details"] or state["transfer_details"]["amount"] is None:
                state["transfer_details"]["amount"] = {
                    "currency": "NGN", "needs_calculation": False}
            elif not state["transfer_details"]["amount"]:
                state["transfer_details"]["amount"] = {
                    "currency": "NGN", "needs_calculation": False}

            if "source_account" not in state["transfer_details"] or state["transfer_details"]["source_account"] is None:
                state["transfer_details"]["source_account"] = {}
            elif not state["transfer_details"]["source_account"]:
                state["transfer_details"]["source_account"] = {}

            if "recipient" in parsed_intent and parsed_intent["recipient"]:
                for key, value in parsed_intent["recipient"].items():
                    if value is not None and value != "":
                        state["transfer_details"]["recipient"][key] = value

                # Auto-normalize bank_name to bank_code if bank_name exists but bank_code doesn't
                recipient_data = state["transfer_details"]["recipient"]
                bank_name = recipient_data.get("bank_name")
                bank_code = recipient_data.get("bank_code")

                if bank_name and not bank_code:
                    from apps.core.src.agent.banking.transfer.utils import normalize_bank_name

                    normalized = normalize_bank_name(bank_name)
                    if normalized.get("code"):
                        print(
                            f"   🏦 Auto-normalized '{bank_name}' → code={normalized['code']}")
                        recipient_data["bank_code"] = normalized["code"]
                        recipient_data["bank_name"] = normalized.get(
                            "normalized", bank_name)

            recipients = self._extract_recipients(parsed_intent)
            if recipients:
                # Normalize bank names for all recipients
                from apps.core.src.agent.banking.transfer.utils import normalize_bank_name

                for recipient in recipients:
                    bank_name = recipient.get("bank_name")
                    bank_code = recipient.get("bank_code")

                    if bank_name and not bank_code:
                        normalized = normalize_bank_name(bank_name)
                        if normalized.get("code"):
                            recipient["bank_code"] = normalized["code"]
                            recipient["bank_name"] = normalized.get(
                                "normalized", bank_name)

                # Ensure recipient dict references the active recipient
                state["transfer_details"]["recipients"] = recipients
                state["transfer_details"]["current_recipient_index"] = 0
                state["transfer_details"]["recipient"] = recipients[0]
                parsed_intent["recipient"] = recipients[0]
            else:
                # Fall back to single-recipient workflow
                single_recipient = state["transfer_details"].get(
                    "recipient") or {}
                state["transfer_details"]["recipients"] = [single_recipient]
                state["transfer_details"]["current_recipient_index"] = 0

            if "amount" in parsed_intent and parsed_intent["amount"]:
                for key, value in parsed_intent["amount"].items():
                    if value is not None and value != "":
                        state["transfer_details"]["amount"][key] = value

            # Normalize amount metadata for multi-recipient transfers
            amount_details = state["transfer_details"]["amount"]
            participants = len(state["transfer_details"]["recipients"])
            if participants > 1:
                expression = str(
                    amount_details.get("calculation_expression") or ""
                ).lower()
                amount_value = self._normalize_amount_value(
                    amount_details.get("value"))

                if amount_value is not None:
                    amount_details["total_value"] = amount_value

                if any(keyword in expression for keyword in ["equal", "even", "each", "between", "among", "split"]):
                    amount_details["split_strategy"] = "equal"
                    amount_details["participants"] = participants
                    if amount_value is not None:
                        per_value = round(amount_value / participants, 2)
                        # Adjust final participant to absorb rounding remainder
                        residual = round(
                            amount_value - per_value * (participants - 1), 2)
                        amount_details["per_recipient_value"] = per_value
                        amount_details["value"] = per_value
                        amount_details["needs_calculation"] = False
                        updated_recipients: List[Dict[str, Any]] = [
                            {
                                **recipient,
                                "allocated_amount": residual if index == participants - 1 else per_value,
                            }
                            for index, recipient in enumerate(state["transfer_details"]["recipients"])
                        ]
                        state["transfer_details"]["recipients"] = updated_recipients
                        current_index = state["transfer_details"].get(
                            "current_recipient_index", 0)
                        state["transfer_details"]["recipient"] = updated_recipients[
                            min(current_index, len(updated_recipients) - 1)
                        ]
                else:
                    amount_details["participants"] = participants
                    if amount_value is not None and "total_value" not in amount_details:
                        amount_details["total_value"] = amount_value

            else:
                amount_value = self._normalize_amount_value(
                    state["transfer_details"]["amount"].get("value")
                )
                if amount_value is not None:
                    state["transfer_details"]["amount"]["value"] = amount_value
                    state["transfer_details"]["amount"]["total_value"] = amount_value
                    if state["transfer_details"]["recipients"]:
                        state["transfer_details"]["recipients"][0]["allocated_amount"] = amount_value
                        state["transfer_details"]["recipient"] = state["transfer_details"]["recipients"][0]

            if "source_account" in parsed_intent and parsed_intent["source_account"]:
                for key, value in parsed_intent["source_account"].items():
                    if value is not None and value != "":
                        state["transfer_details"]["source_account"][key] = value

            if "purpose" in parsed_intent:
                state["transfer_details"]["purpose"] = parsed_intent["purpose"]

            # Initialize dependencies list, handling None from checkpoint state
            state["dependencies"] = parsed_intent.get("dependencies") or []

            print(f"✅ Parsed intent: {json.dumps(parsed_intent, indent=2)}")

        except Exception as e:
            print(f"❌ Error parsing intent: {e}")
            print(f"Traceback: {traceback.format_exc()}")
            if "transfer_details" not in state:
                state["transfer_details"] = {
                    "recipient": {"is_new_beneficiary": True},
                    "amount": {"currency": "NGN", "needs_calculation": False},
                    "source_account": {},
                    "purpose": None,
                    "notes": None,
                }

        # Initialize messages list, handling None from checkpoint state
        if not state.get("messages"):
            state["messages"] = []
        state["messages"].append(HumanMessage(content=user_message))

        state["conversation_stage"] = "enriching"
        return state
