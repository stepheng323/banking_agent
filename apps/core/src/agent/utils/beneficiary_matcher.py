# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false
"""Beneficiary matching utilities using fuzzy matching."""

from typing import List, Dict, Any


def match_beneficiaries(search_term: str, beneficiaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Score beneficiaries by match confidence using fuzzy matching.

    Scoring:
    - Exact name match: 100%
    - Case-insensitive match: 95%
    - Substring match: 80%
    - Starts with match: 75%
    - Partial word match: 60%

    Args:
        search_term: The search term to match against
        beneficiaries: List of beneficiary dictionaries with 'name' and optionally 'nickname'

    Returns:
        List of matches with score >= 60%, sorted by confidence descending
    """
    if not search_term:
        return []

    matches = []
    search_lower = search_term.lower().strip()

    for beneficiary in beneficiaries:
        score = 0
        name = beneficiary.get("name", "").lower()
        nickname = beneficiary.get("nickname", "").lower()

        # Exact match
        if name == search_lower or nickname == search_lower:
            score = 100
        # Case-insensitive match
        elif name == search_lower:
            score = 95
        # Fuzzy matching (simple substring/partial)
        else:
            # Check if search term is in name
            if search_lower in name or search_lower in nickname:
                score = 80
            # Check if name starts with search term
            elif name.startswith(search_lower) or nickname.startswith(search_lower):
                score = 75
            # Partial match
            elif any(word in name.split() for word in search_lower.split()):
                score = 60

        # Frequency boost (more used = more likely)
        frequency = beneficiary.get("frequency", 0)
        if frequency > 5:
            score = min(100, score + 5)

        if score >= 60:
            matches.append({**beneficiary, "confidence_score": score})

    return sorted(matches, key=lambda x: x["confidence_score"], reverse=True)
