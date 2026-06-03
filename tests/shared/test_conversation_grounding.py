from apps.chat.src.agent.orchestrator.conversation.conversation_grounding import (
    build_conversation_grounding,
    conversation_display_name,
    conversation_topic_for_response,
    safe_display_name,
)


def test_conversation_display_name_prefers_profile_first_name() -> None:
    assert (
        conversation_display_name(
            {
                "profile": {"first_name": "Gaines", "full_name": "Wrong Name"},
                "channel_metadata": {"sender_display_name": "Channel Name"},
            }
        )
        == "Gaines"
    )


def test_conversation_display_name_uses_channel_name_when_profile_missing() -> None:
    assert conversation_display_name({"channel_metadata": {"sender_display_name": "Gaines Abiodun"}}) == "Gaines"


def test_safe_display_name_rejects_unsafe_values() -> None:
    assert safe_display_name("https://bad.example") is None
    assert safe_display_name("User123") is None


def test_conversation_grounding_masks_sensitive_history_and_detects_brand_origin() -> None:
    grounding = build_conversation_grounding(
        {
            "history": [
                {"role": "user", "content": "my pin is 1234"},
                {
                    "role": "assistant",
                    "content": (
                        "The name Nenya comes from the Ring of Water from Tolkien's lore. "
                        "We chose it because water suggests liquidity and flow."
                    ),
                },
            ]
        }
    )

    assert grounding["last_topic"] == "brand_origin"
    assert grounding["last_assistant_message"] is not None
    assert all("pin" not in turn["content"].lower() for turn in grounding["recent_turns"])


def test_conversation_grounding_prefers_explicit_assistant_topic_metadata() -> None:
    grounding = build_conversation_grounding(
        {
            "history": [
                {
                    "role": "assistant",
                    "content": "Glad that helped.",
                    "metadata": {"topic": "brand_origin"},
                }
            ]
        }
    )

    assert grounding["last_topic"] == "brand_origin"
    assert grounding["last_assistant_message"] == "Glad that helped."


def test_conversation_topic_for_response_uses_structured_route_metadata() -> None:
    assert (
        conversation_topic_for_response(
            "I'm Nenya AI. I execute real banking tasks fast and carefully.",
            response_key="conversational.identity",
        )
        == "product_identity"
    )
    assert (
        conversation_topic_for_response("I can't help with loans.", semantic_path_shape="capability_boundary_followup")
        == "unsupported_boundary"
    )
