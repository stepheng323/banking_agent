"""User context models for context-aware processing."""
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field


@dataclass
class EntityVocabulary:
    """Vocabulary of user-specific entities for typo correction."""
    
    beneficiary_names: List[str] = field(default_factory=list)
    beneficiary_nicknames: List[str] = field(default_factory=list)
    bank_names: List[str] = field(default_factory=list)
    account_names: List[str] = field(default_factory=list)
    common_variations: Dict[str, List[str]] = field(default_factory=dict)
    
    def get_all_beneficiary_terms(self) -> List[str]:
        """Get all beneficiary-related terms."""
        return self.beneficiary_names + self.beneficiary_nicknames
    
    def get_all_bank_terms(self) -> List[str]:
        """Get all bank-related terms."""
        return self.bank_names


@dataclass
class UserContext:
    """Complete user context for intelligent processing."""
    
    phone_number: str
    accounts: List[Dict[str, Any]] = field(default_factory=list)
    beneficiaries: List[Dict[str, Any]] = field(default_factory=list)
    recent_transactions: List[Dict[str, Any]] = field(default_factory=list)
    entity_names: Optional[EntityVocabulary] = None
    
    def get_total_balance(self) -> float:
        """Calculate total balance across all accounts."""
        return sum(account.get("balance", 0) for account in self.accounts)
    
    def get_beneficiary_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find beneficiary by name (case-insensitive)."""
        name_lower = name.lower()
        for beneficiary in self.beneficiaries:
            if beneficiary.get("name", "").lower() == name_lower:
                return beneficiary
            if beneficiary.get("nickname", "").lower() == name_lower:
                return beneficiary
        return None

