"""Centralized bank name aliases and normalization utilities.

This module provides a single source of truth for Nigerian bank name mappings,
eliminating duplication across BankCacheService and AccountManagementService.
"""

import difflib

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
    "alat": "alat",
    "alt bank": "altbank",
    "altbank": "altbank",
    "citi": "citibank",
    "citibank": "citibank",
    "city bank": "citibank",
    "jaiz": "jaiz",
    "lotus": "lotus",
    "taj": "taj",
    "titan": "titan",
    "titan trust": "titan",
    "globus": "globus",
    "sun trust": "suntrust",
    "suntrust": "suntrust",
    "parallex": "parallex",
    "premium trust": "premiumtrust",
    "premiumtrust": "premiumtrust",
    "optimus": "optimus",
    "rmb": "rand merchant",
    "rand merchant": "rand merchant",
    "zenith": "zenith",
    "heritage": "heritage",
    "standard chartered": "standard chartered",
}

BANK_DISPLAY_NAMES: dict[str, str] = {
    "access": "Access Bank",
    "access bank": "Access Bank",
    "gtbank": "GTBank",
    "uba": "UBA",
    "firstbank": "First Bank",
    "fcmb": "FCMB",
    "stanbic": "Stanbic",
    "ecobank": "Ecobank",
    "fidelity": "Fidelity",
    "wema": "Wema",
    "polaris": "Polaris",
    "keystone": "Keystone",
    "union": "Union",
    "sterling": "Sterling",
    "providus": "Providus",
    "opay": "Opay",
    "palmpay": "PalmPay",
    "kuda": "Kuda",
    "moniepoint": "Moniepoint",
    "zenith": "Zenith Bank",
    "heritage": "Heritage Bank",
    "standard chartered": "Standard Chartered",
}


def normalize_bank_name(name: str) -> str:
    """
    Normalize a bank name or abbreviation to its canonical form.

    Args:
        name: Bank name or abbreviation (e.g., "GTB", "Guaranty Trust Bank")

    Returns:
        Normalized bank identifier (e.g., "gtbank")
    """
    if not name:
        return ""

    normalized = name.lower().strip()
    return BANK_ALIASES.get(normalized, normalized)


def display_bank_name(name: str | None) -> str | None:
    """Return a stable user-facing bank label for known aliases."""
    raw = (name or "").strip()
    if not raw:
        return None

    normalized = normalize_bank_name(raw)
    if normalized in BANK_DISPLAY_NAMES:
        return BANK_DISPLAY_NAMES[normalized]

    return BANK_DISPLAY_NAMES.get(raw.casefold(), raw)


def get_bank_search_terms(name: str) -> list[str]:
    """
    Get search terms for finding a bank in the cache.

    Dynamically derives search terms from BANK_ALIASES to ensure
    a single source of truth.

    Args:
        name: Bank name or abbreviation

    Returns:
        List of terms to search for in bank names
    """
    normalized = normalize_bank_name(name)

    terms = [alias for alias, target in BANK_ALIASES.items() if target == normalized]

    if normalized not in terms:
        terms.insert(0, normalized)

    return terms if terms else [name.lower().strip()]


def find_matching_bank_name(
    search_name: str,
    bank_names: list[str],
) -> str | None:
    """
    Find a matching bank name from a list using exact match, abbreviations,
    and fuzzy matching for typos.

    Args:
        search_name: Bank name or abbreviation to search for
        bank_names: List of bank names to search in

    Returns:
        Matching bank name or None
    """
    if not search_name or not bank_names:
        return None

    normalized = normalize_bank_name(search_name)
    search_terms = get_bank_search_terms(search_name)

    bank_map_lower = {b.lower().strip(): b for b in bank_names}

    for term in search_terms:
        if term in bank_map_lower:
            return bank_map_lower[term]

        for bank_lower, original_name in bank_map_lower.items():
            if term in bank_lower or bank_lower in term:
                return original_name

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

    matches = difflib.get_close_matches(search_clean, cleaned_map.keys(), n=1, cutoff=0.7)

    if matches:
        return cleaned_map[matches[0]]

    return None
