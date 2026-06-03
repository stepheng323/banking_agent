"""Runtime factory for beneficiary-domain workers."""

from banking.beneficiaries.worker import BeneficiaryWorker


def build_beneficiary_worker() -> BeneficiaryWorker:
    """Build the beneficiary worker through the beneficiary domain boundary."""
    return BeneficiaryWorker()
