"""Deterministic, meaning-preserving readiness transcript mutations."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import replace

from scripts.readiness_models import ReadinessScenario, ReadinessTurn

Mutation = Callable[[str], str]


def _lowercase(text: str) -> str:
    return text.lower()


def _remove_terminal_punctuation(text: str) -> str:
    return text.rstrip(".!?")


def _polite_filler(text: str) -> str:
    return f"please {text}" if not text.casefold().startswith(("please ", "abeg ")) else text


def _repeat_first_word(text: str) -> str:
    match = re.match(r"(\w+)(.*)", text)
    return f"{match.group(1)} {match.group(1)}{match.group(2)}" if match else text


def _common_misspelling(text: str) -> str:
    replacements = (
        (r"\btransactions\b", "transctions"),
        (r"\btransaction\b", "transction"),
        (r"\brecipient\b", "reciepient"),
        (r"\bbalance\b", "balnce"),
        (r"\btransfer\b", "trasfer"),
    )
    mutated = text
    for pattern, replacement in replacements:
        candidate, count = re.subn(pattern, replacement, mutated, count=1, flags=re.IGNORECASE)
        if count:
            return candidate
    return mutated


def _pidgin_filler(text: str) -> str:
    normalized = text.casefold()
    if normalized.startswith("show me "):
        return f"abeg {text}"
    if normalized.startswith("what is "):
        return f"wetin be {text[8:]}"
    if normalized.startswith("which "):
        return f"abeg {text}"
    return f"abeg {text}"


MUTATIONS: dict[str, Mutation] = {
    "lowercase": _lowercase,
    "punctuation_removed": _remove_terminal_punctuation,
    "polite_filler": _polite_filler,
    "repeated_word": _repeat_first_word,
    "common_misspelling": _common_misspelling,
    "pidgin_filler": _pidgin_filler,
}


def mutate_turn(turn: ReadinessTurn, mutation_id: str) -> ReadinessTurn:
    mutation = MUTATIONS[mutation_id]
    return replace(turn, text=mutation(turn.text), mutation_id=mutation_id)


def mutate_scenario(scenario: ReadinessScenario, mutation_id: str) -> ReadinessScenario:
    return replace(
        scenario,
        id=f"{scenario.id}[{mutation_id}]",
        turns=tuple(mutate_turn(turn, mutation_id) for turn in scenario.turns),
        tags=tuple(dict.fromkeys((*scenario.tags, "mutated", mutation_id))),
    )


def expand_scenarios(
    scenarios: Iterable[ReadinessScenario],
    *,
    mutation_ids: tuple[str, ...] = tuple(MUTATIONS),
    include_original: bool = True,
) -> tuple[ReadinessScenario, ...]:
    expanded: list[ReadinessScenario] = []
    for scenario in scenarios:
        if include_original:
            expanded.append(scenario)
        expanded.extend(mutate_scenario(scenario, mutation_id) for mutation_id in mutation_ids)
    return tuple(expanded)


__all__ = ["MUTATIONS", "expand_scenarios", "mutate_scenario", "mutate_turn"]
