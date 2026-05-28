from apps.chat.src.agent.workers.__shared__.beneficiary.matcher import BeneficiaryMatcher
from shared.database.models import Beneficiary


def test_beneficiary_matcher_matches_partial_name() -> None:
    matcher = BeneficiaryMatcher()
    beneficiary = Beneficiary(
        account_name="Adebayo Tolu",
        alias=None,
        account_number="0123456789",
        bank_code="044",
        bank_name="Access Bank",
    )

    status, single, candidates = matcher.match("tolu", [beneficiary])

    assert status == "single"
    assert single == beneficiary
    assert candidates == []


def test_beneficiary_matcher_partial_name_ambiguous() -> None:
    matcher = BeneficiaryMatcher()
    first = Beneficiary(
        account_name="Adebayo Tolu",
        alias=None,
        account_number="0123456789",
        bank_code="044",
        bank_name="Access Bank",
    )
    second = Beneficiary(
        account_name="Kunle Tolu",
        alias=None,
        account_number="1234509876",
        bank_code="058",
        bank_name="GTBank",
    )

    status, single, candidates = matcher.match("tolu", [first, second])

    assert status == "clarify"
    assert single is None
    assert len(candidates) == 2


def test_beneficiary_matcher_handles_diacritics() -> None:
    matcher = BeneficiaryMatcher()
    beneficiary = Beneficiary(
        account_name="Tolú Adebayo",
        alias=None,
        account_number="0123456789",
        bank_code="044",
        bank_name="Access Bank",
    )

    status, single, candidates = matcher.match("Tolu", [beneficiary])

    assert status == "single"
    assert single == beneficiary
    assert candidates == []


def test_beneficiary_matcher_short_exact_alias_still_clarifies_when_multiple_related() -> None:
    matcher = BeneficiaryMatcher()
    first = Beneficiary(
        account_name="Tolu Adebayo",
        alias="Tolu",
        account_number="0123456789",
        bank_code="044",
        bank_name="Access Bank",
    )
    second = Beneficiary(
        account_name="Tolu Adeyemi",
        alias="Tolu GTB",
        account_number="1234509876",
        bank_code="058",
        bank_name="GTBank",
    )
    third = Beneficiary(
        account_name="Tolulope Johnson",
        alias="Tolu First",
        account_number="2234509876",
        bank_code="011",
        bank_name="First Bank",
    )

    status, single, candidates = matcher.match("tolu", [first, second, third])

    assert status == "clarify"
    assert single is None
    assert len(candidates) >= 2


def test_beneficiary_matcher_family_variant_mom_matches_saved_mum_alias() -> None:
    matcher = BeneficiaryMatcher()
    beneficiary = Beneficiary(
        account_name="Mercy Johnson",
        alias="Mum",
        account_number="0123456789",
        bank_code="044",
        bank_name="Access Bank",
    )

    status, single, candidates = matcher.match("mom", [beneficiary])

    assert status == "single"
    assert single == beneficiary
    assert candidates == []


def test_beneficiary_matcher_family_variant_dad_matches_saved_daddy_alias() -> None:
    matcher = BeneficiaryMatcher()
    beneficiary = Beneficiary(
        account_name="Kunle Johnson",
        alias="Daddy",
        account_number="0123456789",
        bank_code="044",
        bank_name="Access Bank",
    )

    status, single, candidates = matcher.match("dad", [beneficiary])

    assert status == "single"
    assert single == beneficiary
    assert candidates == []


def test_beneficiary_matcher_family_variant_ambiguity_still_clarifies() -> None:
    matcher = BeneficiaryMatcher()
    first = Beneficiary(
        account_name="Mercy Johnson",
        alias="Mum",
        account_number="0123456789",
        bank_code="044",
        bank_name="Access Bank",
    )
    second = Beneficiary(
        account_name="Amaka Johnson",
        alias="Mummy",
        account_number="1234509876",
        bank_code="058",
        bank_name="GTBank",
    )

    status, single, candidates = matcher.match("mom", [first, second])

    assert status == "clarify"
    assert single is None
    assert len(candidates) == 2
