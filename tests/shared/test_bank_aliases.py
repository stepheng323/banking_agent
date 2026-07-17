"""Unit tests for centralized bank aliases module."""

from shared.utils.bank_aliases import (
    display_bank_name,
    extract_known_bank_names,
    find_matching_bank_name,
    get_bank_search_terms,
    normalize_bank_name,
)


class TestNormalizeBankName:
    """Tests for normalize_bank_name function."""

    def test_normalize_gtb(self):
        """GTB abbreviation should normalize to gtbank."""
        assert normalize_bank_name("GTB") == "gtbank"
        assert normalize_bank_name("gtb") == "gtbank"
        assert normalize_bank_name("  GTB  ") == "gtbank"

    def test_normalize_uba(self):
        """UBA should normalize correctly."""
        assert normalize_bank_name("UBA") == "uba"
        # "united bank" is in aliases
        assert normalize_bank_name("united bank") == "uba"
        # "United Bank for Africa" is NOT in aliases anymore, should return normalized input
        assert normalize_bank_name("United Bank for Africa") == "united bank for africa"

    def test_normalize_access(self):
        """Access bank should return normalized input as it's not an abbreviation."""
        assert normalize_bank_name("access") == "access"
        assert normalize_bank_name("Access Bank") == "access bank"

    def test_normalize_first_bank(self):
        """First bank and FBN should normalize to "firstbank"."""
        assert normalize_bank_name("first bank") == "firstbank"
        assert normalize_bank_name("FBN") == "firstbank"
        assert normalize_bank_name("firstbank") == "firstbank"

    def test_normalize_digital_banks(self):
        """Digital banks should normalize if in aliases."""
        assert normalize_bank_name("Kuda") == "kuda"
        assert normalize_bank_name("OPay") == "opay"
        # "PalmPay" -> "palmpay" (in aliases)
        assert normalize_bank_name("PalmPay") == "palmpay"
        # "palm pay" -> "palm pay" (removed from aliases, so returns as-is)
        assert normalize_bank_name("palm pay") == "palm pay"

    def test_normalize_unknown_returns_original(self):
        """Unknown bank should return the lowercased, stripped version."""
        assert normalize_bank_name("Some Unknown Bank") == "some unknown bank"
        assert normalize_bank_name("Random") == "random"

    def test_normalize_empty_string(self):
        """Empty string should return empty string."""
        assert normalize_bank_name("") == ""


class TestGetBankSearchTerms:
    """Tests for get_bank_search_terms function."""

    def test_gtb_search_terms(self):
        """GTB should return gtbank-related search terms."""
        terms = get_bank_search_terms("GTB")
        assert "gtbank" in terms
        # "guaranty trust" is in aliases
        assert "guaranty trust" in terms

    def test_first_alias_display_name(self):
        """first should normalize to First Bank for user-facing copy."""
        assert normalize_bank_name("first") == "firstbank"
        assert display_bank_name("first") == "First Bank"

    def test_uba_search_terms(self):
        """UBA should return uba-related search terms."""
        terms = get_bank_search_terms("UBA")
        assert "uba" in terms
        assert "united bank" in terms

    def test_unknown_bank_search_terms(self):
        """Unknown bank should return the normalized name in a list."""
        terms = get_bank_search_terms("Random Bank")
        assert "random bank" in terms


class TestDisplayBankName:
    """Tests for user-facing bank labels."""

    def test_display_known_short_aliases(self):
        assert display_bank_name("Access") == "Access Bank"
        assert display_bank_name("access bank") == "Access Bank"
        assert display_bank_name("GTB") == "GTBank"
        assert display_bank_name("UBA") == "UBA"
        assert display_bank_name("opay") == "Opay"

    def test_display_unknown_preserves_input(self):
        assert display_bank_name("Random Bank Nigeria") == "Random Bank Nigeria"
        assert display_bank_name("") is None


class TestExtractKnownBankNames:
    def test_extracts_unique_banks_in_mention_order(self):
        assert extract_known_bank_names("Compare Access Bank, GTB and First Bank") == [
            "Access Bank",
            "GTBank",
            "First Bank",
        ]

    def test_does_not_invent_unknown_banks(self):
        assert extract_known_bank_names("How much is in my unknown bank?") == []


class TestFindMatchingBankName:
    """Tests for find_matching_bank_name function."""

    def test_find_gtb_in_list(self):
        """Should find GTBank when searching for GTB."""
        bank_names = ["GTBank Plc", "Access Bank", "Zenith Bank"]
        result = find_matching_bank_name("GTB", bank_names)
        assert result == "GTBank Plc"

    def test_find_access_in_list(self):
        """Should find Access Bank."""
        bank_names = ["GTBank Plc", "Access Bank", "Zenith Bank"]
        result = find_matching_bank_name("access", bank_names)
        assert result == "Access Bank"

    def test_not_found_returns_none(self):
        """Should return None when bank not found."""
        bank_names = ["GTBank Plc", "Access Bank", "Zenith Bank"]
        result = find_matching_bank_name("Kuda", bank_names)
        assert result is None

    def test_empty_search_returns_none(self):
        """Should return None for empty search."""
        assert find_matching_bank_name("", ["Access Bank"]) is None

    def test_empty_list_returns_none(self):
        """Should return None for empty bank list."""
        assert find_matching_bank_name("GTB", []) is None
