"""Beneficiary fuzzy matcher service."""

import difflib

from shared.database import Beneficiary


class BeneficiaryMatcher:
    """Fuzzy match beneficiary names from a saved list.

    match(name, beneficiaries) -> (status, single, candidates)
      - status: "single" | "clarify" | "ask_details"
      - single: dict when status == "single"
      - candidates: top-N candidates when status == "clarify"
    """

    def __init__(
        self, max_candidates: int = 3, threshold_single: float = 0.9, threshold_min: float = 0.6
    ) -> None:
        self.max_candidates = max_candidates
        self.threshold_single = threshold_single
        self.threshold_min = threshold_min

    def match(
        self, name: str, beneficiaries: list[Beneficiary]
    ) -> tuple[str, Beneficiary | None, list[Beneficiary]]:
        """Match a beneficiary name to a list of beneficiaries."""
        if not name or not beneficiaries:
            return "ask_details", None, []

        name_lower = name.lower().strip()

        # First, check for exact alias match (case-insensitive) - highest priority
        for b in beneficiaries:
            alias = str(b.alias or "").lower().strip() if b.alias else ""
            account_name = str(b.account_name or "").lower().strip() if b.account_name else ""

            # Exact alias match takes priority
            if alias and alias == name_lower:
                return "single", b, []

            # Exact account_name match (secondary priority)
            if account_name and account_name == name_lower:
                return "single", b, []

        startswith_matches = []
        for b in beneficiaries:
            b_name = (b.account_name or "").lower().strip()
            if b_name and len(name_lower) > 2 and b_name.startswith(name_lower):
                startswith_matches.append(b)

        if len(startswith_matches) == 1:
            return "single", startswith_matches[0], []
        elif len(startswith_matches) > 1:
            return "clarify", None, startswith_matches[: self.max_candidates]


        # If no exact match, fall back to fuzzy matching
        names = [str(b.account_name or b.alias or "") for b in beneficiaries]
        ratios = [
            (i, difflib.SequenceMatcher(a=name_lower, b=n.lower()).ratio())
            for i, n in enumerate(names)
        ]
        ratios.sort(key=lambda x: x[1], reverse=True)

        top = [
            (beneficiaries[i], score)
            for i, score in ratios[: self.max_candidates]
            if score >= self.threshold_min
        ]

        if not top:
            return "ask_details", None, []

        if top[0][1] >= self.threshold_single:
            return "single", top[0][0], []

        if len(top) == 1:
            return "single", top[0][0], []

        return "clarify", None, [b for b, _ in top]
