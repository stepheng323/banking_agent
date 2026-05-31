"""Ledger-specific exceptions."""


class LedgerError(Exception):
    """Base ledger error."""


class LedgerEntryConflictError(LedgerError):
    """Raised when a deterministic ledger key exists with incompatible details."""


class LedgerEntryUnbalancedError(LedgerError):
    """Raised when debit and credit lines do not balance."""


LedgerEntryConflict = LedgerEntryConflictError
LedgerEntryUnbalanced = LedgerEntryUnbalancedError
