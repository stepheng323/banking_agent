from apps.chat.src.agent.orchestrator.workflows.gate.utils.semantic_router_llm import (
    SEMANTIC_ROUTER_SYSTEM_PROMPT,
)


def test_semantic_direct_replies_require_specific_grounded_final_copy() -> None:
    assert "Make res specific to the user's message" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "ask exactly one question" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "Never\n   invent a missing amount" in SEMANTIC_ROUTER_SYSTEM_PROMPT
