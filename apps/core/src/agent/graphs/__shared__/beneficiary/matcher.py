"""Beneficiary fuzzy matcher service."""

import difflib
import re
import unicodedata

from shared.database.models import Beneficiary


def _normalize_text(value: str | None) -> str:
    """Normalize names for robust matching across casing/punctuation/diacritics."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", without_marks.lower()).strip()


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

    def match(self, name: str, beneficiaries: list[Beneficiary]) -> tuple[str, Beneficiary | None, list[Beneficiary]]:
        """Match a beneficiary name to a list of beneficiaries."""
        if not name or not beneficiaries:
            return "ask_details", None, []

        normalized_query = _normalize_text(name)
        query_tokens = normalized_query.split()

        # First, collect exact matches (case-insensitive).
        # For short single-token queries (e.g. "tolu"), one exact alias can still be ambiguous
        # if multiple beneficiaries contain the same token.
        exact_matches: list[Beneficiary] = []
        for b in beneficiaries:
            alias = _normalize_text(str(b.alias or ""))
            account_name = _normalize_text(str(b.account_name or ""))

            if (alias and alias == normalized_query) or (account_name and account_name == normalized_query):
                exact_matches.append(b)

        if len(exact_matches) > 1:
            return "clarify", None, exact_matches[: self.max_candidates]

        if len(exact_matches) == 1:
            exact = exact_matches[0]
            if len(query_tokens) == 1:
                related_matches: list[Beneficiary] = [exact]
                for b in beneficiaries:
                    if b is exact:
                        continue
                    alias = _normalize_text(str(b.alias or ""))
                    account_name = _normalize_text(str(b.account_name or ""))
                    if normalized_query and (
                        (alias and normalized_query in alias) or (account_name and normalized_query in account_name)
                    ):
                        related_matches.append(b)
                if len(related_matches) > 1:
                    return "clarify", None, related_matches[: self.max_candidates]
            return "single", exact, []

        startswith_matches = []
        for b in beneficiaries:
            b_name = _normalize_text(str(b.account_name or ""))
            if b_name and len(normalized_query) > 2 and b_name.startswith(normalized_query):
                startswith_matches.append(b)

        if len(startswith_matches) == 1:
            return "single", startswith_matches[0], []
        elif len(startswith_matches) > 1:
            return "clarify", None, startswith_matches[: self.max_candidates]

        contains_matches = []
        if len(normalized_query) >= 3:
            for b in beneficiaries:
                alias = _normalize_text(str(b.alias or ""))
                account_name = _normalize_text(str(b.account_name or ""))
                if normalized_query and (
                    (alias and normalized_query in alias) or (account_name and normalized_query in account_name)
                ):
                    contains_matches.append(b)

        if len(contains_matches) == 1:
            return "single", contains_matches[0], []
        elif len(contains_matches) > 1:
            return "clarify", None, contains_matches[: self.max_candidates]

        # If no exact match, fall back to fuzzy matching
        names = [_normalize_text(str(b.account_name or b.alias or "")) for b in beneficiaries]
        ratios = [(i, difflib.SequenceMatcher(a=normalized_query, b=n).ratio()) for i, n in enumerate(names)]
        ratios.sort(key=lambda x: x[1], reverse=True)

        top = [(beneficiaries[i], score) for i, score in ratios[: self.max_candidates] if score >= self.threshold_min]

        if not top:
            return "ask_details", None, []

        if top[0][1] >= self.threshold_single:
            return "single", top[0][0], []

        if len(top) == 1:
            return "single", top[0][0], []

        return "clarify", None, [b for b, _ in top]
