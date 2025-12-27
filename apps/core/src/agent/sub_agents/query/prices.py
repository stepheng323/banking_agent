"""Known item prices for affordability checks.

This is a curated, limited list. Items not found here will prompt
the user for a price rather than guessing.
"""

from typing import Optional, Dict, Any


# Prices in Naira (NGN) - approximate retail prices
# Last updated: December 2024
KNOWN_ITEMS: Dict[str, Dict[str, Any]] = {
    # Electronics - Apple
    "macbook pro": {"price": 2_500_000, "category": "electronics"},
    "macbook air": {"price": 1_200_000, "category": "electronics"},
    "iphone 15": {"price": 900_000, "category": "electronics"},
    "iphone 15 pro": {"price": 1_400_000, "category": "electronics"},
    "iphone 15 pro max": {"price": 1_800_000, "category": "electronics"},
    "ipad pro": {"price": 1_500_000, "category": "electronics"},
    "ipad air": {"price": 800_000, "category": "electronics"},
    "apple watch": {"price": 450_000, "category": "electronics"},
    "airpods pro": {"price": 180_000, "category": "electronics"},
    
    # Electronics - Samsung
    "samsung galaxy s24": {"price": 750_000, "category": "electronics"},
    "samsung galaxy s24 ultra": {"price": 1_200_000, "category": "electronics"},
    
    # Electronics - General
    "playstation 5": {"price": 550_000, "category": "electronics"},
    "ps5": {"price": 550_000, "category": "electronics"},
    "xbox series x": {"price": 500_000, "category": "electronics"},
}


# Items that require user input (too variable)
VARIABLE_ITEMS = {
    "car", "vehicle", "house", "apartment", "land", "property",
    "laptop", "phone", "television", "tv", "sofa", "furniture"
}


def lookup_item_price(item_name: str) -> Optional[Dict[str, Any]]:
    """Look up a known item's price.
    
    Returns:
        Dict with 'price' and 'category' if found, None otherwise.
    """
    if not item_name:
        return None
    
    normalized = item_name.lower().strip()
    
    # Direct lookup
    if normalized in KNOWN_ITEMS:
        return KNOWN_ITEMS[normalized]
    
    # Partial match (e.g., "a macbook" -> "macbook")
    for known_name, data in KNOWN_ITEMS.items():
        if known_name in normalized or normalized in known_name:
            return data
    
    return None


def is_variable_item(item_name: str) -> bool:
    """Check if item has too variable pricing to guess."""
    if not item_name:
        return False
    
    normalized = item_name.lower().strip()
    
    for variable in VARIABLE_ITEMS:
        if variable in normalized:
            return True
    
    return False


def get_price_disclaimer() -> str:
    """Return disclaimer for price lookups."""
    return "_Prices are approximate and may have changed._"
