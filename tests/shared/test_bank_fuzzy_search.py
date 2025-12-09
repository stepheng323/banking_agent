"""Unit tests for bank fuzzy matching."""

import pytest
from shared.utils.bank_aliases import find_matching_bank_name


class TestBankFuzzyMatching:
    """Tests for fuzzy matching logic in find_matching_bank_name."""

    @pytest.fixture
    def bank_list(self):
        """Standard list of bank names for testing."""
        return [
            "GTBank Plc",
            "United Bank For Africa",
            "Access Bank",
            "Zenith Bank",
            "First Bank of Nigeria",
            "Union Bank",
            "Fidelity Bank",
            "OPay",
            "PalmPay",
            "Kuda Microfinance Bank",
            "Sterling Bank",
            "Wema Bank",
            "Polaris Bank"
        ]

    def test_exact_match(self, bank_list):
        """Exact matches should always work."""
        assert find_matching_bank_name("Access Bank", bank_list) == "Access Bank"
        assert find_matching_bank_name("OPay", bank_list) == "OPay"

    def test_case_insensitive_match(self, bank_list):
        """Case variations should match."""
        assert find_matching_bank_name("access bank", bank_list) == "Access Bank"
        assert find_matching_bank_name("opay", bank_list) == "OPay"
        assert find_matching_bank_name("ZENITH BANK", bank_list) == "Zenith Bank"

    def test_abbreviation_match(self, bank_list):
        """Abbreviations defined in aliases should match."""
        assert find_matching_bank_name("GTB", bank_list) == "GTBank Plc"
        assert find_matching_bank_name("UBA", bank_list) == "United Bank For Africa"
        assert find_matching_bank_name("FBN", bank_list) == "First Bank of Nigeria"

    def test_fuzzy_typo_match(self, bank_list):
        """Typos should be handled by fuzzy matching."""
        # "acess" -> Access
        assert find_matching_bank_name("acess", bank_list) == "Access Bank"
        # "zenit" -> Zenith
        assert find_matching_bank_name("zenit", bank_list) == "Zenith Bank"
        # "fedelity" -> Fidelity
        assert find_matching_bank_name("fedelity", bank_list) == "Fidelity Bank"
        # "palmpay" with space -> PalmPay
        assert find_matching_bank_name("palm pay", bank_list) == "PalmPay"

    def test_partial_match(self, bank_list):
        """Partial names should match."""
        # "Access" -> Access Bank
        assert find_matching_bank_name("Access", bank_list) == "Access Bank"
        # "Zenith" -> Zenith Bank
        assert find_matching_bank_name("Zenith", bank_list) == "Zenith Bank"

    def test_no_match_for_gibberish(self, bank_list):
        """Random string should return None."""
        assert find_matching_bank_name("xyz123random", bank_list) is None
        assert find_matching_bank_name("Super Bank", bank_list) is None
