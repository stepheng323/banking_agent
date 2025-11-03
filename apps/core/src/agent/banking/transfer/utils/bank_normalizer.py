"""Bank name normalization utility for handling typos and variations."""

from typing import Optional, Dict, List
import re

MAJOR_NIGERIAN_BANKS = {
    "GTBank": {
        "variations": ["gtbank", "gtb", "gt bank", "guaranty trust", "guaranty trust bank"],
        "code": "058",
        "official_name": "Guaranty Trust Bank"
    },
    "Access Bank": {
        "variations": ["access", "access bank", "acces bank", "acess bank", "access"],
        "code": "044",
        "official_name": "Access Bank Plc"
    },
    "First Bank": {
        "variations": ["first bank", "firstbank", "firs bank", "first", "fbn"],
        "code": "011",
        "official_name": "First Bank of Nigeria"
    },
    "Zenith Bank": {
        "variations": ["zenith", "zenith bank", "zeneth", "zenit"],
        "code": "057",
        "official_name": "Zenith Bank Plc"
    },
    "UBA": {
        "variations": ["uba", "u.b.a", "united bank for africa", "united bank"],
        "code": "033",
        "official_name": "United Bank for Africa"
    },
    "Fidelity Bank": {
        "variations": ["fidelity", "fidelity bank", "fidelity"],
        "code": "070",
        "official_name": "Fidelity Bank Plc"
    },
    "Stanbic IBTC": {
        "variations": ["stanbic", "stanbic ibtc", "stanbic ibtc bank", "ibtc"],
        "code": "221",
        "official_name": "Stanbic IBTC Bank"
    },
    "Union Bank": {
        "variations": ["union", "union bank", "union bank of nigeria"],
        "code": "032",
        "official_name": "Union Bank of Nigeria"
    },
    "Polaris Bank": {
        "variations": ["polaris", "polaris bank", "pola ris"],
        "code": "076",
        "official_name": "Polaris Bank"
    },
    "Wema Bank": {
        "variations": ["wema", "wema bank"],
        "code": "035",
        "official_name": "Wema Bank Plc"
    },
    "Sterling Bank": {
        "variations": ["sterling", "sterling bank"],
        "code": "232",
        "official_name": "Sterling Bank Plc"
    },
    "FCMB": {
        "variations": ["fcmb", "f.c.m.b", "first city monument", "first city monument bank"],
        "code": "214",
        "official_name": "First City Monument Bank"
    },
    "Ecobank": {
        "variations": ["ecobank", "eco bank", "eco"],
        "code": "050",
        "official_name": "Ecobank Nigeria"
    },
    "Keystone Bank": {
        "variations": ["keystone", "keystone bank", "key stone"],
        "code": "082",
        "official_name": "Keystone Bank"
    },
}

_BANK_CACHE: List[Dict[str, str]] = []


def load_banks_from_flutterwave_response(banks_data: List[Dict]) -> None:
    """
    Load banks from Flutterwave API response into cache.

    This should be called once at startup with the full bank list.

    Args:
        banks_data: List of dicts with 'id', 'code', 'name' from Flutterwave
    """
    global _BANK_CACHE
    _BANK_CACHE = [
        {
            "name": bank["name"],
            "code": bank["code"],
            "id": bank.get("id", "")
        }
        for bank in banks_data
    ]
    print(f"✅ Loaded {len(_BANK_CACHE)} banks into normalizer cache")


def get_all_banks() -> List[Dict[str, str]]:
    """Get all banks from cache."""
    return _BANK_CACHE


def normalize_bank_name(
    raw_input: str,
    use_full_list: bool = True
) -> Dict[str, Optional[str]]:
    """
    Normalize bank name from user input, handling typos and variations.

    Args:
        raw_input: Raw bank name from user (e.g., "gtb", "acces bank", "firs bank")
        use_full_list: If True, searches full Flutterwave list; if False, uses major banks only

    Returns:
        Dict with:
            - normalized: Standardized bank name
            - code: Bank code
            - official_name: Official bank name (same as normalized)
            - confidence: high|medium|low
            - original: Original input
    """
    if not raw_input:
        return {
            "normalized": None,
            "code": None,
            "official_name": None,
            "confidence": "low",
            "original": raw_input
        }

    # Clean input: lowercase, remove extra spaces
    cleaned = re.sub(r'\s+', ' ', raw_input.lower().strip())

    # Strategy 1: Check major banks first (with variations)
    for bank_name, bank_info in MAJOR_NIGERIAN_BANKS.items():
        if cleaned in bank_info["variations"]:
            return {
                "normalized": bank_name,
                "code": bank_info["code"],
                "official_name": bank_info["official_name"],
                "confidence": "high",
                "original": raw_input
            }

    # Strategy 2: Search full Flutterwave bank list (if loaded)
    if use_full_list and _BANK_CACHE:
        # Try exact match (case-insensitive)
        for bank in _BANK_CACHE:
            if cleaned == bank["name"].lower():
                return {
                    "normalized": bank["name"],
                    "code": bank["code"],
                    "official_name": bank["name"],
                    "confidence": "high",
                    "original": raw_input
                }

        # Try partial match (high confidence if contained)
        for bank in _BANK_CACHE:
            bank_name_lower = bank["name"].lower()
            # Check if user input is in bank name or vice versa
            if cleaned in bank_name_lower or bank_name_lower in cleaned:
                return {
                    "normalized": bank["name"],
                    "code": bank["code"],
                    "official_name": bank["name"],
                    "confidence": "medium",
                    "original": raw_input
                }

    # Strategy 3: Fuzzy match on major banks (medium confidence)
    for bank_name, bank_info in MAJOR_NIGERIAN_BANKS.items():
        for variation in bank_info["variations"]:
            if cleaned in variation or variation in cleaned:
                return {
                    "normalized": bank_name,
                    "code": bank_info["code"],
                    "official_name": bank_info["official_name"],
                    "confidence": "medium",
                    "original": raw_input
                }

    # Strategy 4: Fuzzy match on full list (lower confidence)
    if use_full_list and _BANK_CACHE:
        best_match = None
        best_score = 0

        for bank in _BANK_CACHE:
            bank_name_lower = bank["name"].lower()
            # Calculate similarity based on character overlap
            matches = sum(1 for c in cleaned if c in bank_name_lower)
            score = matches / max(len(cleaned), len(bank_name_lower))

            if score > best_score and score > 0.5:  # 50% similarity threshold
                best_score = score
                best_match = bank

        if best_match:
            return {
                "normalized": best_match["name"],
                "code": best_match["code"],
                "official_name": best_match["name"],
                "confidence": "low",
                "original": raw_input
            }

    # Strategy 5: Levenshtein-like matching on major banks (low confidence)
    best_match = None
    best_score = 0

    for bank_name, bank_info in MAJOR_NIGERIAN_BANKS.items():
        for variation in bank_info["variations"]:
            # Count matching characters
            matches = sum(1 for c in cleaned if c in variation)
            score = matches / max(len(cleaned), len(variation))

            if score > best_score and score > 0.6:  # 60% similarity threshold
                best_score = score
                best_match = (bank_name, bank_info)

    if best_match:
        bank_name, bank_info = best_match
        return {
            "normalized": bank_name,
            "code": bank_info["code"],
            "official_name": bank_info["official_name"],
            "confidence": "low",
            "original": raw_input
        }

    # No match found
    return {
        "normalized": None,
        "code": None,
        "official_name": None,
        "confidence": "low",
        "original": raw_input
    }
