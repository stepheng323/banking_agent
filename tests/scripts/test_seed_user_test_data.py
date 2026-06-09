from __future__ import annotations

from scripts.seed_user_test_data import _beneficiary_account_lookup, _seed_beneficiary_specs
from shared.database.enums import BeneficiaryTypeEnum
from shared.database.models import Beneficiary


def test_seed_transfer_beneficiary_lookup_matches_model_blind_index() -> None:
    spec = _seed_beneficiary_specs()[0]

    beneficiary = Beneficiary(
        beneficiary_type=BeneficiaryTypeEnum.TRANSFER.value,
        account_name=spec["account_name"],
        alias=spec["alias"],
        account_number=spec["account_number"],
        bank_code=spec["bank_code"],
        bank_name=spec["bank_name"],
    )

    assert _beneficiary_account_lookup(spec["account_number"]) == beneficiary.account_number_blind_index


def test_seed_transfer_beneficiary_lookups_are_distinct() -> None:
    lookups = {_beneficiary_account_lookup(spec["account_number"]) for spec in _seed_beneficiary_specs()}

    assert len(lookups) == len(_seed_beneficiary_specs())
