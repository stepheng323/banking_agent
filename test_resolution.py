import asyncio
import difflib

# --- Inline Logic from bank_aliases.py ---

BANK_ALIASES: dict[str, str] = {
    "gtb": "gtbank",
    "gt": "gtbank",
    "guaranty trust": "gtbank",
    "uba": "uba",
    "united bank": "uba",
    "fbn": "firstbank",
    "first bank": "firstbank",
    "fcmb": "fcmb",
    "stanbic": "stanbic",
    "ecobank": "ecobank",
    "fidelity": "fidelity",
    "wema": "wema",
    "polaris": "polaris",
    "keystone": "keystone",
    "union": "union",
    "sterling": "sterling",
    "providus": "providus",
    "opay": "opay",
    "palmpay": "palmpay",
    "kuda": "kuda",
    "moniepoint": "moniepoint",
}


def normalize_bank_name(name: str) -> str:
    if not name:
        return ""
    normalized = name.lower().strip()
    return BANK_ALIASES.get(normalized, normalized)


def get_bank_search_terms(name: str) -> list[str]:
    normalized = normalize_bank_name(name)
    terms = [alias for alias, target in BANK_ALIASES.items() if target == normalized]
    if normalized not in terms:
        terms.insert(0, normalized)
    return terms if terms else [name.lower().strip()]


def find_matching_bank_name(search_name: str, bank_names: list[str]) -> str | None:
    if not search_name or not bank_names:
        return None

    normalized = normalize_bank_name(search_name)
    search_terms = get_bank_search_terms(search_name)

    # Debug print
    print(f"DEBUG: Search Name='{search_name}' -> Normalized='{normalized}'")
    print(f"DEBUG: Search Terms={search_terms}")

    bank_map_lower = {b.lower().strip(): b for b in bank_names}

    # 1. Exact match on terms
    for term in search_terms:
        if term in bank_map_lower:
            print(f"DEBUG: Exact match found for term '{term}'")
            return bank_map_lower[term]

        # 2. Substring match
        for bank_lower, original_name in bank_map_lower.items():
            if term in bank_lower or bank_lower in term:
                print(f"DEBUG: Substring match found: '{term}' in '{bank_lower}'")
                return original_name

    # 3. Fuzzy match
    suffixes = [" bank", " plc", " limited", " microfinance bank"]

    def strip_suffixes(s: str) -> str:
        s_clean = s
        for suffix in suffixes:
            s_clean = s_clean.replace(suffix, "")
        return s_clean.strip()

    cleaned_map = {}
    for original_name in bank_names:
        clean = strip_suffixes(original_name.lower())
        if clean:
            cleaned_map[clean] = original_name

    search_clean = strip_suffixes(normalized)
    print(f"DEBUG: Fuzzy search clean='{search_clean}' against {list(cleaned_map.keys())}")

    matches = difflib.get_close_matches(search_clean, cleaned_map.keys(), n=1, cutoff=0.7)

    if matches:
        print(f"DEBUG: Fuzzy match found: '{matches[0]}'")
        return cleaned_map[matches[0]]

    return None


# --- Test Data ---

mock_banks = [
    {"name": "Access Bank", "bank_code": "044"},
    {"name": "OPay", "bank_code": "999991"},
    {"name": "PalmPay", "bank_code": "999992"},
]


async def test_resolution():
    print("--- START TEST ---")
    bank_names = [b["name"] for b in mock_banks]
    print(f"Available Banks: {bank_names}")

    print("\nTest 1: 'Opay'")
    match = find_matching_bank_name("Opay", bank_names)
    print(f"Result: {match}")

    print("\nTest 2: 'opay'")
    match_lower = find_matching_bank_name("opay", bank_names)
    print(f"Result: {match_lower}")


if __name__ == "__main__":
    asyncio.run(test_resolution())
