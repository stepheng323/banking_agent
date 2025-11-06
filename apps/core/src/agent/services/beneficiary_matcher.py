"""Beneficiary fuzzy matcher service."""

from typing import Dict, List, Optional, Tuple
import difflib

from shared.database import Beneficiary



class BeneficiaryMatcher:
    """Fuzzy match beneficiary names from a saved list.

    match(name, beneficiaries) -> (status, single, candidates)
      - status: "single" | "clarify" | "ask_details"
      - single: dict when status == "single"
      - candidates: top-N candidates when status == "clarify"
    """

    def __init__(self, max_candidates: int = 3, threshold_single: float = 0.9, threshold_min: float = 0.6) -> None:
        self.max_candidates = max_candidates
        self.threshold_single = threshold_single
        self.threshold_min = threshold_min

    def match(self, name: str, beneficiaries: List[Beneficiary]) -> Tuple[str, Optional[Beneficiary], List[Beneficiary]]:
        if not name or not beneficiaries:
            return "ask_details", None, []

        names = [str(b.account_name or b.alias or "")
                 for b in beneficiaries]
        ratios = [
            (i, difflib.SequenceMatcher(a=name.lower(), b=n.lower()).ratio())
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
