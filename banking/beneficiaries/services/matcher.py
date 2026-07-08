"""Beneficiary fuzzy matcher service."""

import difflib
import re
import unicodedata

from shared.database.models import Beneficiary

_TOKEN_CANONICAL_ALIASES: dict[str, str] = {
    "mom": "mum",
    "mum": "mum",
    "mummy": "mum",
    "daddy": "dad",
    "dad": "dad",
    "father": "dad",
    "mother": "mum",
    "bro": "brother",
    "bros": "brother",
    "sis": "sister",
}


def _normalize_text(value: str | None) -> str:
    """Normalize names for robust matching across casing/punctuation/diacritics."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    collapsed = re.sub(r"[^a-z0-9]+", " ", without_marks.lower()).strip()
    if not collapsed:
        return ""
    tokens = [_TOKEN_CANONICAL_ALIASES.get(token, token) for token in collapsed.split()]
    return " ".join(tokens)


def _beneficiary_identity_key(beneficiary: Beneficiary) -> tuple[str, str, str, str, str]:
    account_number = re.sub(r"\D+", "", str(beneficiary.account_number or ""))
    return (
        _normalize_text(str(beneficiary.alias or "")),
        _normalize_text(str(beneficiary.account_name or "")),
        account_number,
        _normalize_text(str(beneficiary.bank_name or "")),
        str(beneficiary.bank_code or "").strip().lower(),
    )


def _dedupe_beneficiaries(beneficiaries: list[Beneficiary]) -> list[Beneficiary]:
    unique: list[Beneficiary] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for beneficiary in beneficiaries:
        key = _beneficiary_identity_key(beneficiary)
        if key in seen:
            continue
        seen.add(key)
        unique.append(beneficiary)
    return unique


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

    def exact_matches(self, name: str, beneficiaries: list[Beneficiary]) -> list[Beneficiary]:
        """Return de-duplicated beneficiaries whose alias or account name exactly matches name."""
        if not name or not beneficiaries:
            return []

        normalized_query = _normalize_text(name)
        if not normalized_query:
            return []

        exact_matches: list[Beneficiary] = []
        for beneficiary in _dedupe_beneficiaries(beneficiaries):
            alias = _normalize_text(str(beneficiary.alias or ""))
            account_name = _normalize_text(str(beneficiary.account_name or ""))
            if (alias and alias == normalized_query) or (account_name and account_name == normalized_query):
                exact_matches.append(beneficiary)
        return exact_matches

    @staticmethod
    def _beneficiary_best_ratio(query: str, beneficiary: Beneficiary) -> float:
        alias = _normalize_text(str(beneficiary.alias or ""))
        account_name = _normalize_text(str(beneficiary.account_name or ""))
        candidate_names = [value for value in (alias, account_name) if value]
        if not candidate_names:
            return 0.0

        best_score = 0.0
        for candidate in candidate_names:
            base_ratio = difflib.SequenceMatcher(a=query, b=candidate).ratio()
            sorted_query = " ".join(sorted(query.split()))
            sorted_candidate = " ".join(sorted(candidate.split()))
            token_ratio = difflib.SequenceMatcher(a=sorted_query, b=sorted_candidate).ratio()
            best_score = max(best_score, base_ratio, token_ratio)
        return best_score

    def match(self, name: str, beneficiaries: list[Beneficiary]) -> tuple[str, Beneficiary | None, list[Beneficiary]]:
        """Match a beneficiary name to a list of beneficiaries."""
        if not name or not beneficiaries:
            return "ask_details", None, []

        beneficiaries = _dedupe_beneficiaries(beneficiaries)
        normalized_query = _normalize_text(name)
        query_tokens = normalized_query.split()

        # First, collect exact matches (case-insensitive).
        exact_matches = self.exact_matches(name, beneficiaries)

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
                    b_tokens = set(alias.split() + account_name.split())
                    if normalized_query in b_tokens:
                        related_matches.append(b)
                if len(related_matches) > 1:
                    return "clarify", None, related_matches[: self.max_candidates]
            return "single", exact, []

        startswith_matches = []
        for b in beneficiaries:
            b_name = _normalize_text(str(b.account_name or ""))
            alias = _normalize_text(str(b.alias or ""))
            if len(normalized_query) > 2 and (
                (b_name and b_name.startswith(normalized_query)) or
                (alias and alias.startswith(normalized_query))
            ):
                startswith_matches.append(b)

        if len(startswith_matches) == 1:
            return "single", startswith_matches[0], []
        elif len(startswith_matches) > 1:
            return "clarify", None, startswith_matches[: self.max_candidates]

        token_matches = []
        if len(normalized_query) >= 3:
            for b in beneficiaries:
                alias = _normalize_text(str(b.alias or ""))
                account_name = _normalize_text(str(b.account_name or ""))
                b_tokens = set(alias.split() + account_name.split())

                matched = False
                for q_token in query_tokens:
                    if q_token in b_tokens:
                        matched = True
                        break
                    # only prefix match if token is long enough
                    if len(q_token) >= 3 and any(t.startswith(q_token) for t in b_tokens):
                        matched = True
                        break
                if matched:
                    token_matches.append(b)

        if len(token_matches) == 1:
            return "single", token_matches[0], []
        elif len(token_matches) > 1:
            return "clarify", None, token_matches[: self.max_candidates]

        # If no exact/token match, fall back to fuzzy matching but with strict length checks
        # to avoid "ayo" matching "adebayo" (ratio 0.6). We raise the min threshold dynamically.
        min_thresh = self.threshold_min if len(normalized_query) > 4 else 0.75

        ratios = [
            (index, self._beneficiary_best_ratio(normalized_query, beneficiary))
            for index, beneficiary in enumerate(beneficiaries)
        ]
        ratios.sort(key=lambda x: x[1], reverse=True)

        top = [(beneficiaries[i], score) for i, score in ratios[: self.max_candidates] if score >= min_thresh]

        if not top:
            return "ask_details", None, []

        if top[0][1] >= self.threshold_single:
            return "single", top[0][0], []

        if len(top) == 1:
            return "single", top[0][0], []

        return "clarify", None, [b for b, _ in top]
