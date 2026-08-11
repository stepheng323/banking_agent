"""Conversation-level adversarial mutations.

Message mutations preserve a single utterance shape.  These transformations
exercise the harder cases: an extra turn, a repeated callback, a split
instruction or an unrelated interruption.  They are opt-in because the
expected semantic outcome is scenario-specific.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from scripts.readiness_models import (
    ReadinessConversationMutation,
    ReadinessExpectation,
    ReadinessScenario,
    ReadinessTurn,
)


@dataclass(frozen=True)
class ConversationMutation:
    id: str
    apply: Callable[[tuple[ReadinessTurn, ...]], tuple[ReadinessTurn, ...]]
    semantics_preserved: bool


def _insert_ack_before_last(turns: tuple[ReadinessTurn, ...]) -> tuple[ReadinessTurn, ...]:
    if not turns:
        return turns
    return (*turns[:-1], ReadinessTurn("Okay"), turns[-1])


def _insert_balance_interrupt_before_last(turns: tuple[ReadinessTurn, ...]) -> tuple[ReadinessTurn, ...]:
    if not turns:
        return turns
    return (*turns[:-1], ReadinessTurn("Wait, what's my balance?"), turns[-1])


def _repeat_last(turns: tuple[ReadinessTurn, ...]) -> tuple[ReadinessTurn, ...]:
    if not turns:
        return turns
    last = turns[-1]
    return (*turns, replace(last, mutation_id="repeated_last"))


def _split_last_conjunction(turns: tuple[ReadinessTurn, ...]) -> tuple[ReadinessTurn, ...]:
    if not turns:
        return turns
    last = turns[-1]
    parts = [part.strip() for part in last.text.split(" and ", 1)]
    if len(parts) != 2 or not all(parts):
        return turns
    return (
        *turns[:-1],
        ReadinessTurn(parts[0], ReadinessExpectation(expect_no_money_movement=True)),
        ReadinessTurn(parts[1], ReadinessExpectation(expect_no_money_movement=True)),
    )


CONVERSATION_MUTATIONS: dict[str, ConversationMutation] = {
    "insert_ack_before_last": ConversationMutation(
        "insert_ack_before_last", _insert_ack_before_last, semantics_preserved=True
    ),
    "insert_balance_interrupt": ConversationMutation(
        "insert_balance_interrupt", _insert_balance_interrupt_before_last, semantics_preserved=False
    ),
    "repeat_last": ConversationMutation("repeat_last", _repeat_last, semantics_preserved=False),
    "split_last_conjunction": ConversationMutation(
        "split_last_conjunction", _split_last_conjunction, semantics_preserved=False
    ),
}


def mutate_conversation(
    scenario: ReadinessScenario,
    mutation_id: ReadinessConversationMutation | str,
) -> ReadinessScenario:
    """Return a deterministic conversation mutation with an auditable ID."""

    mutation = CONVERSATION_MUTATIONS[mutation_id]
    return replace(
        scenario,
        id=f"{scenario.id}[conversation-{mutation_id}]",
        turns=mutation.apply(scenario.turns),
        tags=tuple(dict.fromkeys((*scenario.tags, "conversation_mutated", mutation_id))),
    )


def mutate_scenarios(
    scenarios: tuple[ReadinessScenario, ...],
    mutation_id: ReadinessConversationMutation | str,
) -> tuple[ReadinessScenario, ...]:
    """Apply one deterministic conversation mutation to every selected scenario."""

    return tuple(mutate_conversation(scenario, mutation_id) for scenario in scenarios)


__all__ = ["CONVERSATION_MUTATIONS", "ConversationMutation", "mutate_conversation", "mutate_scenarios"]
