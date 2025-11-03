# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false, reportOptionalOperand=false, reportOptionalMemberAccess=false, reportTypedDictNotRequiredAccess=false
"""Clarification nodes for the transfer agent."""
import json
from typing import Any

from langchain_core.messages import HumanMessage, AIMessage

from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.banking.transfer.prompts import CLARIFICATION_PROMPTS
from apps.core.src.agent.banking.tools.transfer_tools import calculate_amount


class ClarificationNodes:
    """Nodes for handling clarifications and user responses."""

    def __init__(self, llm: Any) -> None:
        """Initialize with LLM instance."""
        self.llm = llm

    async def clarification_agent_node(self, state: TransferState) -> TransferState:
        """Ask questions to fill missing information."""
        print("❓ CLARIFICATION AGENT: Generating question...")

        transfer_details = state.get("transfer_details", {})
        amount_details = transfer_details.get("amount", {}) or {}
        recipients = transfer_details.get("recipients") or []
        current_index = transfer_details.get("current_recipient_index", 0)
        active_recipient = (
            recipients[current_index]
            if recipients and 0 <= current_index < len(recipients)
            else transfer_details.get("recipient", {}) or {}
        )
        recipient_name = active_recipient.get("name", "the recipient")
        allocated_amount = active_recipient.get("allocated_amount")
        if allocated_amount is None:
            allocated_amount = (
                amount_details.get("per_recipient_value")
                or amount_details.get("value")
                or amount_details.get("total_value")
            )

        question = "I'm ready to help you send! I just need a bit more information to complete the transfer."

        if state.get("clarifications_needed"):
            clarification = state["clarifications_needed"][0]

            if clarification["type"] == "ambiguous_recipient":
                options = clarification["options"]
                options_text = "\n".join([
                    f"{i+1}) {opt['name']} ({opt['bank_name']} ****{opt['account_number'][-4:]})"
                    for i, opt in enumerate(options)
                ])

                prompt = CLARIFICATION_PROMPTS["ambiguous_recipient"].format(
                    options=options_text)
                response = await self.llm.ainvoke([HumanMessage(content=prompt)])
                if hasattr(response, "content") and isinstance(response.content, str):
                    question = response.content
                else:
                    question = f"I found a couple of options. Which one should I send to?\n{options_text}\n\nJust reply with the number."

                state["pending_clarification"] = {
                    "type": "ambiguous_recipient",
                    "options": options,
                    "recipient_index": current_index,
                }

        elif state.get("missing_slots"):
            missing_slot = state["missing_slots"][0]

            if missing_slot == "recipient.account_number":
                if len(recipients) > 1:
                    resolved_names = [
                        (recipient.get("resolved_account_name")
                         or recipient.get("name") or "another recipient")
                        for idx, recipient in enumerate(recipients)
                        if idx != current_index and recipient.get("account_number") and recipient.get("bank_code")
                    ]
                    resolved_clause = (
                        f" I already have {', '.join(resolved_names)}'s details." if resolved_names else ""
                    )
                    amount_clause = (
                        f"I'm set to send ₦{allocated_amount:,.2f} to {recipient_name}. "
                        if allocated_amount
                        else "I'm ready to send money. "
                    )
                    question = (
                        f"{amount_clause}I just need {recipient_name}'s account number to finish the transfer.{resolved_clause}"
                    )
                else:
                    prompt = CLARIFICATION_PROMPTS["recipient.account_number"].format(
                        recipient_name=recipient_name)
                    response = await self.llm.ainvoke([HumanMessage(content=prompt)])
                    if hasattr(response, "content") and isinstance(response.content, str):
                        question = response.content
                    else:
                        question = f"I'm ready to help you send! To complete this transfer, I'll need the recipient's account number. Could you share it with me?"
                state["pending_clarification"] = {
                    "type": "recipient.account_number",
                    "recipient_index": current_index,
                }

            elif missing_slot == "recipient.bank_code":
                if len(recipients) > 1:
                    question = (
                        f"Great, I have {recipient_name}'s account number. Which bank is it with so I can finish their transfer?"
                    )
                else:
                    prompt = CLARIFICATION_PROMPTS["recipient.bank_code"]
                    response = await self.llm.ainvoke([HumanMessage(content=prompt)])
                    if hasattr(response, "content") and isinstance(response.content, str):
                        question = response.content
                    else:
                        question = f"Great! Which bank is the account with? You can tell me the bank name like 'GTBank' or 'First Bank'."
                state["pending_clarification"] = {
                    "type": "recipient.bank_code",
                    "recipient_index": current_index,
                }

            elif missing_slot == "amount.value":
                prompt = CLARIFICATION_PROMPTS["amount.value"].format(
                    recipient_name=recipient_name)
                response = await self.llm.ainvoke([HumanMessage(content=prompt)])
                if hasattr(response, "content") and isinstance(response.content, str):
                    question = response.content
                else:
                    question = f"How much would you like to send? You can tell me the amount (e.g., ₦5,000 or just 5k)."
                state["pending_clarification"] = {"type": "amount.value"}

            elif missing_slot == "source_account.account_id":
                accounts = state.get("user_accounts", []) or []

                lines = ["*Which account would you like to use?*", ""]
                for index, account in enumerate(accounts):
                    account_number = account.get("account_number", "")
                    bank_name = account.get("bank_name", account.get(
                        "account_name", "Unknown Bank"))

                    last_four_digits = account_number[-4:] if len(
                        account_number) >= 4 else "****"
                    masked_account_number = f"(...{last_four_digits})"

                    lines.append(
                        f"{index + 1} *{bank_name}* {masked_account_number}")

                accounts_text = "\n".join(lines)

                prompt = CLARIFICATION_PROMPTS["source_account.account_id"].format(
                    accounts=accounts_text)
                response = await self.llm.ainvoke([HumanMessage(content=prompt)])
                if hasattr(response, "content") and isinstance(response.content, str):
                    question = response.content
                else:
                    question = accounts_text
                state["pending_clarification"] = {
                    "type": "source_account.account_id",
                    "options": accounts,
                }

        transfer_details["recipient"] = active_recipient
        state["transfer_details"] = transfer_details

        if not state.get("messages"):
            state["messages"] = []
        state["messages"].append(AIMessage(content=question))
        state["response"] = question
        state["conversation_stage"] = "gathering"
        state["waiting_for_user_response"] = True

        pending_clarification = state.get("pending_clarification")
        clarification_type = pending_clarification.get(
            "type") if isinstance(pending_clarification, dict) else None
        state["awaiting_clarification"] = True
        state["clarification_type"] = clarification_type

        print(f"💬 Question: {question}")
        return state

    async def parse_clarification_response(self, state: TransferState) -> TransferState:
        """Parse user's response to clarification question."""
        print("📝 Parsing user response...")

        user_response = state["message"]
        pending = state.get("pending_clarification", {})
        clarification_type = pending.get("type")

        transfer_details = state.get("transfer_details", {}) or {}
        recipients = transfer_details.get("recipients") or []
        current_index = pending.get(
            "recipient_index",
            transfer_details.get("current_recipient_index", 0),
        )

        if recipients and 0 <= current_index < len(recipients):
            active_recipient = recipients[current_index]
        else:
            if "recipient" not in transfer_details or not transfer_details["recipient"]:
                transfer_details["recipient"] = {}
            active_recipient = transfer_details["recipient"]

        if not state.get("messages"):
            state["messages"] = []
        state["messages"].append(HumanMessage(content=user_response))

        if clarification_type == "ambiguous_recipient":
            options = pending["options"]
            parse_prompt = f"""Parse the user's selection from these options:
{json.dumps(options, indent=2)}
User response: "{user_response}"
Extract the selected option index (0-based). Return JSON: {{"selected_index": <number>}}
Return only valid JSON."""
            response = await self.llm.ainvoke([HumanMessage(content=parse_prompt)])

            if not hasattr(response, "content") or not isinstance(response.content, str):
                print("Error: Invalid response from LLM")
                return state
            response_text = response.content

            if "```json" in response_text:
                response_text = response_text.split(
                    "```json")[1].split("```")[0].strip()

            try:
                parsed = json.loads(response_text)
                selected_index = parsed["selected_index"]
                if 0 <= selected_index < len(options):
                    selected = options[selected_index]
                    active_recipient.update({
                        "matched_beneficiary_id": selected["id"],
                        "name": selected["name"],
                        "account_number": selected["account_number"],
                        "bank_code": selected["bank_code"],
                        "bank_name": selected["bank_name"],
                        "confidence_score": selected["confidence_score"],
                        "is_new_beneficiary": False,
                    })
                    state["clarifications_needed"] = [
                        c for c in state.get("clarifications_needed", [])
                        if c["type"] != "ambiguous_recipient"
                    ]
            except Exception as exc:
                print(f"Error parsing selection: {exc}")

        elif clarification_type == "recipient.account_number":
            missing_slots = state.get("missing_slots", [])
            accounts = state.get("user_accounts", []) or []

            parse_prompt = f"""Extract ALL transfer information from the user's response: "{user_response}"



The user might provide any combination of:
- Account number (e.g., "0760505261", "1234567890")
- Bank name/code (e.g., "Access Bank", "GTBank", "First Bank")
- Amount (e.g., "5000", "5k", "₦10,000", "ten thousand")

Extract everything the user mentioned and return JSON:
{{
  "account_number": "<10-digit number if found, otherwise null>",
  "bank_name": "<bank name if found, otherwise null>",
  "bank_code": "<bank code if inferred, otherwise null>",
  "amount": {{
    "value": <numeric amount if found, otherwise null>,
    "expression": "<original amount expression if found, otherwise null>"
  }}
}}

Common bank mappings:
- GTBank/GTB → code "058"
- First Bank/FirstBank → code "011"
- Access Bank/Access → code "044"
- Zenith Bank → code "057"
- UBA → code "033"
- Fidelity Bank → code "070"
- Stanbic IBTC → code "221"

For amounts, extract numeric values from expressions like:
- "5000", "5k" → 5000
- "₦10,000", "10 thousand" → 10000
- "ten thousand naira" → 10000

Return ONLY valid JSON, no explanation."""
            response = await self.llm.ainvoke([HumanMessage(content=parse_prompt)])

            if not hasattr(response, "content") or not isinstance(response.content, str):
                print("Error: Invalid response from LLM")
                return state
            response_text = response.content

            if "```json" in response_text:
                response_text = response_text.split(
                    "```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split(
                    "```")[1].split("```")[0].strip()

            try:
                parsed = json.loads(response_text)

                if parsed.get("account_number"):
                    active_recipient["account_number"] = parsed["account_number"]
                    if recipients and 0 <= current_index < len(recipients):
                        recipients[current_index]["account_number"] = parsed["account_number"]
                    if "recipient.account_number" in missing_slots:
                        state["missing_slots"] = [
                            s for s in missing_slots if s != "recipient.account_number"]
                        print(
                            f"✅ Account number extracted: {parsed['account_number']}")

                if parsed.get("bank_name") or parsed.get("bank_code"):
                    if parsed.get("bank_code"):
                        active_recipient["bank_code"] = parsed["bank_code"]
                    if parsed.get("bank_name"):
                        active_recipient["bank_name"] = parsed["bank_name"]

                    if recipients and 0 <= current_index < len(recipients):
                        if parsed.get("bank_code"):
                            recipients[current_index]["bank_code"] = parsed["bank_code"]
                        if parsed.get("bank_name"):
                            recipients[current_index]["bank_name"] = parsed["bank_name"]

                    if "recipient.bank_code" in missing_slots:
                        state["missing_slots"] = [s for s in state.get(
                            "missing_slots", []) if s != "recipient.bank_code"]
                        print(
                            f"✅ Bank information also extracted: {parsed.get('bank_name', parsed.get('bank_code', ''))}")

                amount_data = parsed.get("amount", {})
                if amount_data.get("value") or amount_data.get("expression"):
                    phone_number = state["phone_number"]
                    account_id = accounts[0]["id"] if len(
                        accounts) == 1 else None

                    amount_expr = amount_data.get(
                        "expression") or str(amount_data.get("value"))
                    calc_result = calculate_amount.invoke({
                        "expression": amount_expr,
                        "phone_number": phone_number,
                        "account_id": account_id,
                    })

                    if calc_result.get("success") and calc_result.get("amount"):
                        amount_value = float(calc_result["amount"])
                        amount_details = transfer_details.setdefault(
                            "amount", {})
                        amount_details["value"] = amount_value
                        amount_details["total_value"] = amount_value
                        amount_details["calculation_expression"] = calc_result.get(
                            "expression")
                        amount_details["needs_calculation"] = False

                        if recipients:
                            recipients[0]["allocated_amount"] = amount_value

                        if "amount.value" in missing_slots:
                            state["missing_slots"] = [s for s in state.get(
                                "missing_slots", []) if s != "amount.value"]
                        if "amount.source_data" in (state.get("missing_slots") or []):
                            state["missing_slots"] = [s for s in state.get(
                                "missing_slots", []) if s != "amount.source_data"]
                        print(f"✅ Amount also extracted: ₦{amount_value:,.2f}")

            except Exception as exc:
                print(f"Error parsing comprehensive response: {exc}")
                import traceback
                traceback.print_exc()

        elif clarification_type == "recipient.bank_code":
            missing_slots = state.get("missing_slots", [])
            accounts = state.get("user_accounts", []) or []

            bank_map = {
                "gtb": "058", "gtbank": "058",
                "first": "011", "first bank": "011",
                "access": "044", "access bank": "044",
                "zenith": "057", "zenith bank": "057",
            }
            response_lower = user_response.lower()
            resolved = False
            for key, value in bank_map.items():
                if key in response_lower:
                    active_recipient["bank_code"] = value
                    active_recipient["bank_name"] = key.title() + " Bank"
                    if recipients and 0 <= current_index < len(recipients):
                        recipients[current_index]["bank_code"] = value
                        recipients[current_index]["bank_name"] = key.title() + \
                            " Bank"
                    resolved = True
                    break

            if not resolved:
                parse_prompt = f"""Extract bank information and check for amount from: "{user_response}"
Return JSON: {{"bank_code": "<code>", "bank_name": "<name>", "amount": {{"value": <numeric or null>, "expression": "<expression or null>"}}}}"""
                response = await self.llm.ainvoke([HumanMessage(content=parse_prompt)])

                if not hasattr(response, "content") or not isinstance(response.content, str):
                    print("Error: Invalid response from LLM")
                    return state
                response_text = response.content

                if "```json" in response_text:
                    response_text = response_text.split(
                        "```json")[1].split("```")[0].strip()
                elif "```" in response_text:
                    response_text = response_text.split(
                        "```")[1].split("```")[0].strip()

                try:
                    parsed = json.loads(response_text)
                    active_recipient["bank_code"] = parsed["bank_code"]
                    active_recipient["bank_name"] = parsed.get(
                        "bank_name", "").title()
                    if recipients and 0 <= current_index < len(recipients):
                        recipients[current_index]["bank_code"] = parsed["bank_code"]
                        recipients[current_index]["bank_name"] = parsed.get(
                            "bank_name", "").title()

                    amount_data = parsed.get("amount", {})
                    if amount_data.get("value") or amount_data.get("expression"):
                        phone_number = state["phone_number"]
                        account_id = accounts[0]["id"] if len(
                            accounts) == 1 else None
                        amount_expr = amount_data.get(
                            "expression") or str(amount_data.get("value"))
                        calc_result = calculate_amount.invoke({
                            "expression": amount_expr,
                            "phone_number": phone_number,
                            "account_id": account_id,
                        })

                        if calc_result.get("success") and calc_result.get("amount"):
                            amount_value = float(calc_result["amount"])
                            amount_details = transfer_details.setdefault(
                                "amount", {})
                            amount_details["value"] = amount_value
                            amount_details["total_value"] = amount_value
                            amount_details["needs_calculation"] = False

                            if "amount.value" in missing_slots:
                                state["missing_slots"] = [
                                    s for s in missing_slots if s != "amount.value"]
                            print(
                                f"✅ Amount also extracted: ₦{amount_value:,.2f}")

                except Exception as exc:
                    print(f"Error parsing bank code: {exc}")

            state["missing_slots"] = [s for s in state.get(
                "missing_slots", []) if s != "recipient.bank_code"]

        elif clarification_type == "amount.value":
            phone_number = state["phone_number"]
            accounts = state.get("user_accounts", []) or []
            account_id = accounts[0]["id"] if len(accounts) == 1 else None

            calc_result = calculate_amount.invoke({
                "expression": user_response,
                "phone_number": phone_number,
                "account_id": account_id,
            })

            if calc_result.get("success") and calc_result.get("amount"):
                amount_value = float(calc_result["amount"])
                amount_details = transfer_details.setdefault("amount", {})
                split_hint = calc_result.get(
                    "split_hint") or amount_details.get("split_strategy")
                if split_hint:
                    amount_details["split_strategy"] = split_hint
                participants = len(recipients) if recipients else 1

                amount_details["total_value"] = amount_value
                amount_details["calculation_expression"] = calc_result.get(
                    "expression")
                amount_details["needs_calculation"] = False

                if participants > 1 and amount_details.get("split_strategy") == "equal":
                    even_amount = round(amount_value / participants, 2)
                    amounts = [even_amount] * participants
                    remainder = round(
                        amount_value - even_amount * (participants - 1), 2)
                    if amounts:
                        amounts[-1] = remainder
                    amount_details["per_recipient_value"] = even_amount
                    amount_details["value"] = even_amount
                    for idx, recipient in enumerate(recipients):
                        recipient["allocated_amount"] = amounts[idx] if idx < len(
                            amounts) else even_amount
                else:
                    amount_details["value"] = amount_value
                    if recipients:
                        recipients[0]["allocated_amount"] = amount_value

                state["missing_slots"] = [s for s in state.get(
                    "missing_slots", []) if s != "amount.value"]
                if "amount.source_data" in (state.get("missing_slots") or []):
                    state["missing_slots"].remove("amount.source_data")
                print(f"✅ Amount parsed: ₦{amount_value:,.2f}")
            else:
                error_msg = calc_result.get("error", "Could not parse amount")
                print(f"❌ Error parsing amount: {error_msg}")

        elif clarification_type == "source_account.account_id":
            options = pending.get("options", [])
            parse_prompt = f"""Parse the user's selection for source account given these options:
{json.dumps(options, indent=2)}
User response: "{user_response}"
Extract the selected option index (0-based). Return JSON: {{"selected_index": <number>}}"""
            response = await self.llm.ainvoke([HumanMessage(content=parse_prompt)])

            if not hasattr(response, "content") or not isinstance(response.content, str):
                print("Error: Invalid response from LLM")
                return state
            response_text = response.content

            if "```json" in response_text:
                response_text = response_text.split(
                    "```json")[1].split("```")[0].strip()
            try:
                parsed = json.loads(response_text)
                selected_index = parsed["selected_index"]
                if 0 <= selected_index < len(options):
                    selected = options[selected_index]
                    transfer_details["source_account"] = {
                        "account_id": selected.get("id"),
                        "account_name": selected.get(
                            "account_name", selected.get("bank_name", "")),
                        "balance": selected.get("balance"),
                    }
                state["missing_slots"] = [s for s in state.get(
                    "missing_slots", []) if s != "source_account.account_id"]
            except Exception as exc:
                print(f"Error parsing account selection: {exc}")

        recipient_data = dict(active_recipient)

        if recipients and 0 <= current_index < len(recipients):
            recipients[current_index] = recipient_data
            transfer_details["recipients"] = recipients
            transfer_details["current_recipient_index"] = current_index
        transfer_details["recipient"] = recipient_data
        state["transfer_details"] = transfer_details

        print(
            f"📋 Parsed recipient: account={recipient_data.get('account_number')}, bank={recipient_data.get('bank_code')}, bank_name={recipient_data.get('bank_name')}")
        print(
            f"📋 Updated transfer_details recipient: {transfer_details.get('recipient', {}).get('account_number')}, {transfer_details.get('recipient', {}).get('bank_code')}")
        print(f"📋 Remaining missing slots: {state.get('missing_slots', [])}")

        state["pending_clarification"] = None
        state["waiting_for_user_response"] = False
        state["awaiting_clarification"] = False
        state["clarification_type"] = None
        return state
