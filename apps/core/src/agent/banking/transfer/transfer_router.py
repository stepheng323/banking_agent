"""
Router to decide between Simple Transfer Agent and Intelligent Transfer Agent.

Simple scenarios (use SimpleTransferAgent):
- "Send 5000 to 0123456789 GTBank"
- "Send 10k to John"
- "Transfer 50,000 to mom"

Complex scenarios (use IntelligentTransferAgent):
- "Send to that mechanic from last month"
- "Send 200k using savings first, checking if needed"
- "Send 10% of my salary account to church"
- "Send to whoever I paid for generator repairs"
"""

import re
from typing import Literal
from apps.core.src.agent.banking.transfer import (
    IntelligentTransferAgent, TransferAgent
)


_SIMPLE_AGENT_CACHE = None
_INTELLIGENT_AGENT_CACHE = None


def classify_transfer_complexity(message: str) -> Literal["simple", "complex"]:
    """
    Classify whether a transfer request is simple or complex.

    Args:
        message: User's message

    Returns:
        "simple" or "complex"
    """
    message_lower = message.lower()

    historical_patterns = [
        r"\blast\s+(month|week|time)",
        r"\b(yesterday|earlier|previous|ago)\b",
        r"\bthat\s+(person|guy|man|woman|mechanic|pastor|teacher)",
        r"\bwho(ever)?\s+i\s+(paid|sent|gave)",
        r"\bfrom\s+(last|previous)",
    ]

    for pattern in historical_patterns:
        if re.search(pattern, message_lower):
            print(
                f"   ℹ️  Complex: Historical reference detected - '{pattern}'")
            return "complex"

    multi_account_patterns = [
        r"\buse\s+\w+\s+(first|then)",
        r"\bfrom\s+\w+\s+(and|or|then)",
        r"\bpool",
        r"\bcombine\s+(accounts|from)",
        r"\bif\s+needed",
        r"\bsavings\s+(first|and|or)",
        r"\bavoid\s+(my|using)",
    ]

    for pattern in multi_account_patterns:
        if re.search(pattern, message_lower):
            print(
                f"   ℹ️  Complex: Multi-account/pooling detected - '{pattern}'")
            return "complex"

    # 3. Percentage or dynamic calculations
    calculation_patterns = [
        r"\d+\s*%",
        r"\bpercent\b",
        r"\bhalf\s+of",
        r"\ball\s+of",
        r"\bentire\b",
        r"\brest\s+of",
    ]

    for pattern in calculation_patterns:
        if re.search(pattern, message_lower):
            print(
                f"   ℹ️  Complex: Dynamic calculation detected - '{pattern}'")
            return "complex"

    # 4. Conditional logic
    conditional_patterns = [
        r"\bif\s+(i\s+)?(haven't|didn't|have not)",
        r"\bunless",
        r"\bbut\s+only\s+if",
        r"\bcheck\s+(if|whether)",
    ]

    for pattern in conditional_patterns:
        if re.search(pattern, message_lower):
            print(f"   ℹ️  Complex: Conditional logic detected - '{pattern}'")
            return "complex"

    # 5. Vague descriptions instead of names
    vague_patterns = [
        r"\bthat\s+\w+\s+(for|from|who)",
        r"\bthe\s+(person|guy|man|woman)\s+(who|for|from)",
        r"\bwhoever",
        r"\bsomeone",
    ]

    for pattern in vague_patterns:
        if re.search(pattern, message_lower):
            print(f"   ℹ️  Complex: Vague recipient description - '{pattern}'")
            return "complex"

    # Simple indicators - use simple agent

    # Has account number (usually simple)
    if re.search(r"\b\d{10}\b", message_lower):
        print("   ✅ Simple: Direct account number provided")
        return "simple"

    # Has explicit bank name (usually simple)
    banks = ["gtbank", "access", "zenith",
             "first bank", "uba", "fidelity", "stanbic"]
    if any(bank in message_lower for bank in banks):
        print("   ✅ Simple: Bank name explicitly provided")
        return "simple"

    # Simple amount + simple name pattern
    simple_pattern = r"(send|transfer|pay)\s+(\₦?\d+k?|\d+,?\d*)\s+to\s+\w+"
    if re.search(simple_pattern, message_lower):
        print("   ✅ Simple: Standard transfer pattern detected")
        return "simple"

    # Default to simple for straightforward requests
    print("   ✅ Simple: No complex patterns detected")
    return "simple"


async def route_transfer_request(
    phone_number: str,
    message: str,
    message_id: str
) -> dict:
    """
    Route transfer request to appropriate agent and return response with state.

    Args:
        phone_number: User's phone number
        message: User's message
        message_id: Unique message ID

    Returns:
        Dictionary with:
            - response: str
            - awaiting_clarification: bool
            - clarification_type: Optional[str]
            - conversation_stage: Optional[str]
            - outbox_messages: List[Dict[str, Any]]
    """
    # Filter out special internal messages that shouldn't be processed as transfers
    special_messages = {"CLEAR_STATE", "GLOBAL_CANCEL",
                        "CLEAR_PENDING_SWITCH", "CLEAR_PENDING_SWITCH_FINAL"}
    if message in special_messages:
        print(f"   ⚠️  Ignoring special message: {message}")
        # For special messages, just return a no-op response
        return {
            "response": "",
            "awaiting_clarification": False,
            "clarification_type": None,
            "conversation_stage": "completed",
            "outbox_messages": [],
        }

    # CONTINUE_PENDING_TRANSFER is allowed to proceed - it will load checkpoint and continue

    complexity = classify_transfer_complexity(message)

    if complexity == "complex":
        print("\n🧠 Routing to INTELLIGENT AGENT (complex scenario)")

        # Use cached agent instance to reuse checkpointer connection
        global _intelligent_agent_cache
        if _intelligent_agent_cache is None:
            print("   🔧 Creating new IntelligentTransferAgent instance (first time)")
            _intelligent_agent_cache = IntelligentTransferAgent()
            # Ensure checkpointer is ready
            await _intelligent_agent_cache._ensure_checkpointer()

        agent = _intelligent_agent_cache

        # Get config and load existing state from checkpoint
        config = agent._get_config(phone_number, message_id)

        try:
            # Check for existing checkpoint
            checkpoint = await agent.graph.aget_state(config)

            # Determine if we have meaningful state to continue from
            has_state = False
            is_new_transfer_request = False

            # Check if message is a NEW transfer request (contains transfer keywords)
            transfer_keywords = ["send", "transfer",
                                 "pay", "send money", "transfer money"]
            message_lower = message.lower()
            is_new_transfer_request = any(
                kw in message_lower for kw in transfer_keywords)

            if checkpoint and checkpoint.values:
                td = checkpoint.values.get('transfer_details', {})
                clarification_type = checkpoint.values.get(
                    'clarification_type')
                awaiting_clarification = checkpoint.values.get(
                    'awaiting_clarification', False)

                # If checkpoint has pending clarification but user sent a NEW transfer request,
                # treat it as a new transfer and clear the old checkpoint
                if awaiting_clarification and is_new_transfer_request:
                    if clarification_type == "pin_confirmation":
                        # For PIN, only clear if it's clearly a new transfer request
                        print(
                            f"   🔄 NEW transfer detected while PIN pending - clearing old checkpoint")
                        clear_state = {
                            "phone_number": phone_number,
                            "message": "CLEAR_STATE",
                            "message_id": message_id,
                            "transfer_details": {},
                            "awaiting_clarification": False,
                            "clarification_type": None,
                            "pending_clarification": None,
                            "conversation_stage": None,
                            "messages": [],
                        }
                        await agent.graph.ainvoke(clear_state, config)
                        has_state = False
                    elif clarification_type not in ("pin_confirmation", "pending_switch_confirmation"):
                        # For other clarifications, if user sends new transfer request, start fresh
                        print(
                            f"   🔄 NEW transfer detected while waiting for {clarification_type} - clearing old checkpoint")
                        clear_state = {
                            "phone_number": phone_number,
                            "message": "CLEAR_STATE",
                            "message_id": message_id,
                            "transfer_details": {},
                            "awaiting_clarification": False,
                            "clarification_type": None,
                            "pending_clarification": None,
                            "conversation_stage": None,
                            "messages": [],
                        }
                        await agent.graph.ainvoke(clear_state, config)
                        has_state = False
                    else:
                        # Keep state for PIN/pending_switch (might be continuation)
                        has_state = (
                            bool(td.get('recipient', {}).get('account_number')) or
                            bool(checkpoint.values.get(
                                'pending_clarification'))
                        )
                else:
                    has_state = (
                        bool(td.get('recipient', {}).get('account_number')) or
                        bool(checkpoint.values.get('pending_clarification'))
                    )

            if has_state:
                # CONTINUATION: Merge checkpoint with new message
                print(f"   🔄 Continuing intelligent transfer")
                initial_state = dict(checkpoint.values)
                initial_state.update({
                    "phone_number": phone_number,
                    "message": message,
                    "message_id": message_id,
                })
            else:
                # NEW: Start fresh
                print(f"   🆕 New intelligent conversation")
                initial_state = {
                    "phone_number": phone_number,
                    "message": message,
                    "message_id": message_id,
                }

            result = await agent.graph.ainvoke(initial_state, config)
            return {
                "response": result.get("response", "I've processed your request."),
                "awaiting_clarification": result.get("awaiting_clarification", False),
                "clarification_type": result.get("clarification_type"),
                "conversation_stage": result.get("conversation_stage", "completed"),
                "outbox_messages": result.get("outbox_messages", []),
            }
        except Exception as e:
            print(f"❌ Intelligent agent error: {e}")
            import traceback
            traceback.print_exc()
            return {
                "response": f"I encountered an issue: {str(e)}",
                "awaiting_clarification": False,
                "clarification_type": None,
                "conversation_stage": "error",
                "outbox_messages": [],
            }

    else:
        print("\n⚡ Routing to SIMPLE AGENT (standard transfer)")

        # Use cached agent instance to reuse checkpointer connection
        global _SIMPLE_AGENT_CACHE
        if _SIMPLE_AGENT_CACHE is None:
            print("   🔧 Creating new SimpleTransferAgent instance (first time)")
            _SIMPLE_AGENT_CACHE = TransferAgent()
            # Ensure checkpointer is ready
            await _SIMPLE_AGENT_CACHE._ensure_checkpointer()

        agent = _SIMPLE_AGENT_CACHE

        # Get config and load existing state from checkpoint
        config = agent._get_config(phone_number, message_id)

        # DEBUG: Show thread_id being used
        print(
            f"   🔑 THREAD_ID: {config.get('configurable', {}).get('thread_id')}")

        try:
            # Check for existing checkpoint
            checkpoint = await agent.graph.aget_state(config)

            # DEBUG: Log what we found
            print(f"   📋 CHECKPOINT DEBUG:")
            if checkpoint:
                print(f"      ✓ Checkpoint object exists")
                if checkpoint.values:
                    print(f"      ✓ Has values dict")
                    td = checkpoint.values.get('transfer_details', {})
                    print(f"      - transfer_details exists: {bool(td)}")
                    if td and isinstance(td, dict):
                        recipient = td.get('recipient', {})
                        print(f"      - recipient exists: {bool(recipient)}")
                        if recipient:
                            acct = recipient.get('account_number')
                            print(f"      - account_number: '{acct}'")
                    pending = checkpoint.values.get('pending_clarification')
                    print(f"      - pending_clarification: {pending}")
                    print(
                        f"      - Keys: {list(checkpoint.values.keys())[:5]}")
                else:
                    print(f"      ❌ checkpoint.values is empty/None")
            else:
                print(f"      ❌ No checkpoint found")

            # Determine if we have meaningful state to continue from
            has_state = False
            is_new_transfer_request = False

            # Check if message is a NEW transfer request (contains transfer keywords)
            transfer_keywords = ["send", "transfer",
                                 "pay", "send money", "transfer money"]
            message_lower = message.lower()
            is_new_transfer_request = any(
                kw in message_lower for kw in transfer_keywords)

            if checkpoint and checkpoint.values:
                td = checkpoint.values.get('transfer_details', {})
                clarification_type = checkpoint.values.get(
                    'clarification_type')
                awaiting_clarification = checkpoint.values.get(
                    'awaiting_clarification', False)

                # If checkpoint has pending clarification but user sent a NEW transfer request,
                # treat it as a new transfer and clear the old checkpoint
                # Exception: PIN confirmation - only clear if message contains transfer keywords
                # (PIN could be entered as digits which shouldn't trigger reset)
                if awaiting_clarification and is_new_transfer_request:
                    if clarification_type == "pin_confirmation":
                        # For PIN, only clear if it's clearly a new transfer request
                        # (not just digits that might be PIN entry)
                        print(
                            f"   🔄 NEW transfer detected while PIN pending - clearing old checkpoint")
                        clear_state = {
                            "phone_number": phone_number,
                            "message": "CLEAR_STATE",
                            "message_id": message_id,
                            "transfer_details": {},
                            "awaiting_clarification": False,
                            "clarification_type": None,
                            "pending_clarification": None,
                            "conversation_stage": None,
                            "messages": [],
                        }
                        await agent.graph.ainvoke(clear_state, config)
                        has_state = False
                    elif clarification_type not in ("pin_confirmation", "pending_switch_confirmation"):
                        # For other clarifications (source account, amount, etc.),
                        # if user sends new transfer request, start fresh
                        print(
                            f"   🔄 NEW transfer detected while waiting for {clarification_type} - clearing old checkpoint")
                        clear_state = {
                            "phone_number": phone_number,
                            "message": "CLEAR_STATE",
                            "message_id": message_id,
                            "transfer_details": {},
                            "awaiting_clarification": False,
                            "clarification_type": None,
                            "pending_clarification": None,
                            "conversation_stage": None,
                            "messages": [],
                        }
                        await agent.graph.ainvoke(clear_state, config)
                        has_state = False
                    else:
                        # Keep state for PIN/pending_switch (might be continuation)
                        has_state = (
                            bool(td.get('recipient', {}).get('account_number')) or
                            bool(checkpoint.values.get(
                                'pending_clarification'))
                        )
                else:
                    has_state = (
                        bool(td.get('recipient', {}).get('account_number')) or
                        bool(checkpoint.values.get('pending_clarification'))
                    )

            print(f"   → has_state decision: {has_state}")
            print(f"   → is_new_transfer_request: {is_new_transfer_request}")

            if has_state:
                # CONTINUATION: Merge checkpoint with new message
                print(f"   🔄 CONTINUING with checkpoint state")
                initial_state = dict(checkpoint.values)
                initial_state.update({
                    "phone_number": phone_number,
                    "message": message,
                    "message_id": message_id,
                })
            else:
                # NEW: Start fresh
                print(f"   🆕 NEW - no meaningful checkpoint data")
                initial_state = {
                    "phone_number": phone_number,
                    "message": message,
                    "message_id": message_id,
                }

            result = await agent.graph.ainvoke(initial_state, config)

            # DEBUG: Check what was actually saved to checkpoint after execution
            print(f"\n   📊 POST-EXECUTION CHECKPOINT CHECK:")
            post_checkpoint = await agent.graph.aget_state(config)
            if post_checkpoint and post_checkpoint.values:
                print(f"      ✓ Checkpoint saved")
                print(
                    f"      - Checkpoint ID: {post_checkpoint.config.get('configurable', {}).get('checkpoint_id', 'N/A')}")
                print(
                    f"      - Keys saved: {list(post_checkpoint.values.keys())}")
                td_post = post_checkpoint.values.get('transfer_details', {})
                print(f"      - transfer_details: {bool(td_post)}")
                if td_post:
                    print(
                        f"        - recipient.account_number: {td_post.get('recipient', {}).get('account_number')}")
                print(
                    f"      - clarification_type: {post_checkpoint.values.get('clarification_type')}")
                print(
                    f"      - awaiting_clarification: {post_checkpoint.values.get('awaiting_clarification')}")
            else:
                print(f"      ❌ No checkpoint saved!")

            return {
                "response": result.get("response", "I couldn't process your transfer."),
                "awaiting_clarification": result.get("awaiting_clarification", False),
                "clarification_type": result.get("clarification_type"),
                "conversation_stage": result.get("conversation_stage", "completed"),
                "outbox_messages": result.get("outbox_messages", []),
            }
        except Exception as e:
            print(f"❌ Transfer agent error: {e}")
            import traceback
            traceback.print_exc()
            return {
                "response": f"I encountered an error: {str(e)}",
                "awaiting_clarification": False,
                "clarification_type": None,
                "conversation_stage": "error",
                "outbox_messages": [],
            }
