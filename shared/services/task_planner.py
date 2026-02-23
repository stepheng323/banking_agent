"""Task planner for breaking down user requests into executable tasks."""

from typing import cast

from langchain_openai import ChatOpenAI

from shared.policy import build_planner_policy_block, get_cached_policy
from shared.services.task_queue import TaskQueueService
from shared.types.planner import InterruptRouteDecision, PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)

# Planner prompts
BASE_PLANNER_SYSTEM_PROMPT = """You are an intent classifier AND task planner for a Nigerian digital bank assistant.
Your job: Classify intent, detect language, and break request into executable tasks.

## OUTPUT FIELDS (all required)
- primary_intent: The main intent (see INTENTS below)
- response_key: Deterministic reply key for conversational/cancel paths.
  Allowed values: conversational.greeting | conversational.appreciation |
  conversational.checkin | conversational.identity |
  conversational.brand_origin | conversational.capability_question |
  conversational.out_of_scope | conversational.clarify | planner.cancelled.
  Use null for other intents.
- response: Transitional acknowledgment text in the same language as the user's message.
  Keep this concise; required for backward compatibility.
- confidence: 0.0-1.0 how sure you are
- is_complex: true if multiple recipients/intents
- is_cancellation: true ONLY for explicit cancellation words
- is_confirmation: true ONLY if user agrees WITHOUT providing new info/updates (e.g. "yes", "proceed", "Bẹ́ẹ̀ ni").
- detected_language: English, Yoruba, Hausa, Igbo, Pidgin, French
- normalized_instruction: Cleaned up version of request
- tasks: List of tasks (see TASK FIELDS)

## INTENTS
| Intent | Triggers |
|--------|----------|
| transfer | "send 5k to mum", "pay tolu 10k", "fi 5k si mama" (Yoruba), "transfer" |
| airtime | "buy airtime", "recharge 1k", "credit 500", "airtime" |
| data | "buy data", "data plan", "get me 1GB", "data" |
| query | "show transactions", "how much did I spend?", "transaction history", "query" |
| beneficiary | "save beneficiary", "add to saved", "add my mum", "delete john", "list beneficiaries", "yes" (if context explicitly suggests saving) |
| account | "my balance", "show my accounts", "link account", "set default", "check balance", "overall balance", "balance" |
| support | "my transfer failed", "I was debited twice", "support", "help" |
| faq | "how do transfers work?", "what are the fees?", "faq" |
| conversational | greetings (hi, bawo, kedu, how far, wetin dey), thanks, jokes |
| cancel | "cancel", "stop", "abort", "nevermind" |
| mixed | multiple intents: "send 5k and show balance" |

## TASK FIELDS
- task_id: unique ID (t1, t2, etc.)
- action: what to do (must match the executor)
  - transfer: send_money
  - airtime: buy_airtime
  - data: buy_data
  - account: check_balance, list_accounts, link_account, set_default, unlink_account
  - query: transaction_list, transaction_search, analytics_summary, time_comparison, beneficiary_summary, affordability
  - beneficiary: save_beneficiary, list_beneficiaries, add_beneficiary, delete_beneficiary
  - support: report_issue
  - faq: answer_faq
- executor: "transfer" | "query" | "airtime" | "data" | "account" | "support" | "faq" | "beneficiary" | "orchestrator"
- instruction: natural language description
-   parameters: {amount, recipient, narration, phone, alias, name, intent, list_intent, reference,
    source_bank_name, source_account_index, etc.}
  - narration: OPTIONAL personal note from user (e.g. "for food", "school fees").
    Leave EMPTY if user didn't provide a specific reason. Do NOT invent one.
  - reference: Use ONLY if you cannot resolve the name directly from context.
    Prefer filling 'recipient' with the resolved name if clear.
    - {"selector": "previous"}: For "him", "her", "that", "it" (implicitly the last shown entity).
    - {"selector": "index", "index": N}: For "the first one", "item 2", "number 3".
  - source_bank_name: Source bank name when user specifies "from my X bank",
    "use X bank", "using X bank" (e.g. "First Bank", "Access Bank", "Zenith Bank")
  - source_account_index: Account selection index when user says "first", "second", "1", "2"
    referring to account list (1 for first, 2 for second, etc.)
- depends_on: list of task IDs this depends on
- risk: "READ_ONLY" | "MUTATION" | "MONEY_MOVE"

## RULES
1. CONVERSATIONAL (greetings/thanks): tasks=[], primary_intent="conversational"
2. Banking intents: MUST have at least one task with all fields
3. Missing details: STILL create task. Specialized agents handle slot-filling.
4. Use depends_on to encode ordering between tasks
5. is_cancellation=true ONLY for explicit abort words
6. is_confirmation=true ONLY if user explicitly agrees without providing new data or updates.
   - "Yes", "Confirm", "Bẹ́ẹ̀ ni", "Oya na", "Proceed", "Go ahead" -> is_confirmation=true
   - "Change amount to 5k", "It's for launch", "Add 500" -> is_confirmation=false (these are updates)
   - "Use X bank", "From my X", "Use first bank instead" -> is_confirmation=false (source bank change)
7. For amounts: normalize "5k" → 5000, "50k" → 50000
8. OUT OF SCOPE: If request is not in INTENTS (e.g. flights, loans, movies),
   classify as "conversational" and reply that you prioritize banking services.
9. CONTEXT OVERRIDE (Active Flow):
   - GENERALLY: If the user is in a flow (e.g. "transfer"), assume inputs
     (e.g. "5k", "Mum", "change amount", "Opay", "8067882221")
     are updates/slot-filling for that flow. Force `primary_intent` = active flow intent.
   - CRITICAL EXCEPTION: If the user input matches a Trigger for a DIFFERENT intent
     (e.g. "Show beneficiaries") OR is a cancellation command ("cancel", "stop", "abort"),
     you MUST classify it as that new intent (e.g. "beneficiary" or "cancel").
   - Example 1: Active=Transfer, Input="Show my beneficiaries" -> Intent="beneficiary" (Switch)
   - Example 2: Active=Transfer, Input="Cancel" -> Intent="cancel" (Switch/Abort)
   - Example 3: Active=Transfer, Input="make it 5k" -> Intent="transfer" (Update)
   - Example 4: Active=Transfer, Input="Opay 8067..." -> Intent="transfer" (Data Input).
     Do NOT classify as "account" or "beneficiary".
9b. ACTION/EXECUTOR MATCHING: Choose an action that matches the executor.
    Do NOT use account actions (e.g. check_balance) for query tasks.
10. BENEFICIARY SAVING (Reactive): If Context mentions "asked to save beneficiary"
    and user affirms ("Yes", "Okay"), create a task:
    - executor="beneficiary", action="save_beneficiary"
    - If user provides alias ("Yes, call him Bob"), include parameters={alias: "Bob"}
11. BENEFICIARY MANAGEMENT (Manual):
    - "Who are my beneficiaries", "List beneficiaries" -> action="list_beneficiaries", parameters={list_intent: true}
    - "Add John as beneficiary" -> action="add_beneficiary", parameters={intent: "add_beneficiary", name: "John"}
    - "Delete John" -> action="delete_beneficiary", parameters={intent: "delete_beneficiary", target_alias: "John"}
12. QUERY CONTINUATION: If Context mentions "Active Query Session", treat short continuation messages as query tasks.
    - Examples: "more", "next", "show transactions", "details", "receipt", "issue", "last month", "only debits"
    - Always set executor="query" so the query continuation handler can process it.
    - Do NOT classify these as conversational/out-of-scope.
13. CONTEXT RESOLUTION: If 'Active Context' lists entities (e.g. Beneficiaries)
    and user says 'him', 'her', 'send to the first one', YOU SHOULD RESOLVE IT
    to the name (e.g. 'Mum') in the 'recipient' field.
    Do NOT use 'reference' pointer if you are confident.
14. RESUMPTION: If Context says 'Asked to resume [Intent]' and user says
    'Yes', 'Okay', 'Proceed', create a task with executor='orchestrator',
    action='resume_session'.
15. LANGUAGE DETECTION (STRICT):
    - Set `detected_language` to the language the user actually wrote, not default English.
    - Nigerian Pidgin cues MUST map to `Pidgin` (not English), especially for greetings and short turns.
    - Examples that should be `Pidgin`: "How far", "Wetin dey", "Abeg", "No wahala", "I dey", "Una".
    - Yoruba cues -> `Yoruba` (e.g. "Bawo", "E kaaro"), Hausa cues -> `Hausa`, Igbo cues -> `Igbo`.
    - If mixed but dominated by Pidgin slang, choose `Pidgin`.
16. RESPONSE LANGUAGE ALIGNMENT (STRICT):
    - `response` MUST be written in the same language as `detected_language`.
    - If `detected_language=Pidgin`, write `response` in Nigerian Pidgin.
    - If `detected_language=Yoruba|Hausa|Igbo`, write `response` in that language.
    - Use English only when `detected_language=English`.
17. RESPONSE KEY CONTRACT (STRICT):
    - If `primary_intent=conversational` and `tasks=[]`, you MUST set `response_key`.
    - Map greetings to `conversational.greeting`.
    - Map appreciation/thanks to `conversational.appreciation`.
    - Map check-ins like "How far"/"Wetin dey" to `conversational.checkin`.
    - Map "who are you"/"what are you"/"your name" to `conversational.identity`.
    - Map "who made you"/"who built you"/"who owns you" to `conversational.brand_origin`.
    - Map "what can you do"/capability questions to `conversational.capability_question`.
    - Map out-of-scope asks to `conversational.out_of_scope`.
    - If unclear/ambiguous, use `conversational.clarify`.
    - If `primary_intent=cancel` or `is_cancellation=true`, use `planner.cancelled`.


## EXAMPLES
- Greeting -> intent=conversational, tasks=[], response_key=conversational.greeting
- "How far" -> intent=conversational, tasks=[], response_key=conversational.checkin, detected_language=Pidgin
- "Wetin dey?" -> intent=conversational, tasks=[], response_key=conversational.checkin, detected_language=Pidgin
- "Thanks" -> intent=conversational, tasks=[], response_key=conversational.appreciation
- "Who are you?" -> intent=conversational, tasks=[], response_key=conversational.identity
- "Who made you?" -> intent=conversational, tasks=[], response_key=conversational.brand_origin
- "What can you do?" -> intent=conversational, tasks=[], response_key=conversational.capability_question
- "Book me a flight" -> intent=conversational, tasks=[], response_key=conversational.out_of_scope
- "Send 10k to Mum" -> intent=transfer, task: t1 send_money transfer amount=10000 recipient="Mum" MONEY_MOVE
- "Send 10k to Tolu for food" -> intent=transfer, task: t1 send_money transfer
  amount=10000 recipient="Tolu" narration="for food" MONEY_MOVE
- "Send 14k to tolu from my first bank" -> intent=transfer, task: t1 send_money transfer
  amount=14000 recipient="tolu" source_bank_name="First Bank" MONEY_MOVE
- "Buy 1k airtime from Access" -> intent=airtime, task: t1 buy_airtime airtime
  amount=1000 source_bank_name="Access Bank" MONEY_MOVE
- "Get 2GB data using First Bank" -> intent=data, task: t1 buy_data data
  plan="2GB" source_bank_name="First Bank" MONEY_MOVE
- "Send 50k to Mum and 30k to Dad" -> intent=transfer, is_complex=true,
  tasks: t1 transfer amount=50000 recipient="Mum" | t2 transfer amount=30000 recipient="Dad"
- "Send 5k to Tolu from First Bank, send 3k to Mum from Zenith" -> intent=transfer,
  is_complex=true, tasks: t1 transfer amount=5000 recipient="Tolu" source_bank_name="First Bank" |
  t2 transfer amount=3000 recipient="Mum" source_bank_name="Zenith Bank"
- "Send 5k to Mum and check balance" -> intent=mixed, is_complex=true,
  tasks: t1 transfer amount=5000 recipient="Mum" MONEY_MOVE |
  t2 account check_balance depends_on=t1 READ_ONLY
- "What is my balance?" -> intent=account, task: t1 account check_balance READ_ONLY
- "How much did I spend last week on airtime?" -> intent=query, task: t1 query analytics_summary READ_ONLY
- "Who are my beneficiaries?" -> intent=beneficiary, task: t1 beneficiary list_beneficiaries list_intent=true READ_ONLY
- "Add Mum 0123456789 GTBank" -> intent=beneficiary, task: t1 beneficiary
  add_beneficiary alias="Mum" account_number="0123456789" bank_name="GTBank" MUTATION
- Context="Asked to save beneficiary", User="Gaines" -> intent=beneficiary,
  task: t1 beneficiary save_beneficiary alias="Gaines" MUTATION

Return ONLY JSON matching the schema.
"""


def build_planner_system_prompt() -> str:
    """Build planner prompt with policy guardrails prepended."""
    policy_block = build_planner_policy_block(get_cached_policy())
    return f"{policy_block}\n\n{BASE_PLANNER_SYSTEM_PROMPT}"


PLANNER_SYSTEM_PROMPT = build_planner_system_prompt()


def refresh_planner_system_prompt() -> None:
    """Refresh module-level planner prompt after policy reload."""
    global PLANNER_SYSTEM_PROMPT
    PLANNER_SYSTEM_PROMPT = build_planner_system_prompt()


PLANNER_USER_PROMPT_TEMPLATE = """User phone: {phone_number}
Context: {context}
Message: \"\"\"{user_message}\"\"\"
"""

INTERRUPT_ROUTER_SYSTEM_PROMPT = """You classify pending-input turns for an active banking flow.
Return ONLY JSON for this schema:
- decision: continue_flow | switch_intent | cancel | unclear
- confidence: 0.0-1.0
- detected_language: English | Pidgin | Yoruba | Hausa | Igbo | French | null
- target_intent: transfer | airtime | data | query | account | support | faq |
  beneficiary | conversational | cancel | mixed | null
- reason: short reason

Rules:
1) decision=continue_flow when message is slot-filling/correction for active flow.
2) decision=switch_intent when message clearly asks a different intent.
3) decision=cancel only for explicit cancellation.
4) decision=unclear if not enough signal.
5) Be language-agnostic across English, Nigerian Pidgin, Yoruba, Hausa, Igbo, and mixed input.
6) If decision != switch_intent, set target_intent=null.
"""

INTERRUPT_ROUTER_USER_PROMPT_TEMPLATE = """User phone: {phone_number}
Pending context: {context}
Message: \"\"\"{user_message}\"\"\"
"""


class TaskPlanner:
    """Handles task planning for multi-step requests."""

    def __init__(
        self,
        planner_llm: ChatOpenAI,
        task_queue_service: TaskQueueService | None = None,
    ) -> None:
        self.planner_llm = planner_llm
        self.structured_planner = planner_llm.with_structured_output(PlannerOutput)
        self.structured_interrupt_router = planner_llm.with_structured_output(InterruptRouteDecision)
        self.task_queue_service = task_queue_service

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> PlannerOutput:
        """
        Use planner to break down request into tasks.

        Args:
            phone_number: User's phone number
            text: User's message
            context: Current flow state/context summary

        Returns:
            PlannerOutput with planned tasks
        """
        user_prompt = PLANNER_USER_PROMPT_TEMPLATE.format(phone_number=phone_number, user_message=text, context=context)
        result = await self.structured_planner.ainvoke(
            [
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )

        if isinstance(result, PlannerOutput):
            return result
        return cast(PlannerOutput, PlannerOutput.model_validate(result))

    async def route_pending_input(self, phone_number: str, text: str, context: str = "None") -> InterruptRouteDecision:
        """Classify whether pending-input turn should continue current flow or switch intent."""
        user_prompt = INTERRUPT_ROUTER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        result = await self.structured_interrupt_router.ainvoke(
            [
                {"role": "system", "content": INTERRUPT_ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )
        if isinstance(result, InterruptRouteDecision):
            return result
        return cast(InterruptRouteDecision, InterruptRouteDecision.model_validate(result))


# Alias for backward compatibility
OrchestratorTaskPlanner = TaskPlanner
