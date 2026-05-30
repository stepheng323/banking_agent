from types import SimpleNamespace

from banking.beneficiaries.repositories.beneficiary_repository import BeneficiaryRepository


def _repo_with_rows(rows):
    repo = BeneficiaryRepository.__new__(BeneficiaryRepository)

    async def get_by_user(user_id: str, beneficiary_type: str | None = None):
        del user_id, beneficiary_type
        return rows

    repo.get_by_user = get_by_user
    return repo


async def test_should_suggest_beneficiary_matches_mono_bank_code() -> None:
    repo = _repo_with_rows(
        [
            SimpleNamespace(
                account_number="8162511023",
                bank_code="044",
                bank_name="Access Bank",
            )
        ]
    )

    should_suggest = await repo.should_suggest_beneficiary("user-1", "8162511023", "044")

    assert should_suggest is False


async def test_should_suggest_beneficiary_matches_missing_code_by_normalized_bank_name() -> None:
    repo = _repo_with_rows(
        [
            SimpleNamespace(
                account_number="8162511023",
                bank_code=None,
                bank_name="Access   Bank PLC",
            )
        ]
    )

    should_suggest = await repo.should_suggest_beneficiary(
        "user-1",
        "8162511023",
        None,
        bank_name="access bank plc",
    )

    assert should_suggest is False
