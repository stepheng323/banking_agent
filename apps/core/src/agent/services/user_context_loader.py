"""User context loading service."""
from typing import Dict, Any, List
from apps.core.src.agent.models.user_context import UserContext, EntityVocabulary
from apps.core.src.agent.banking.tools.account_tools import get_user_accounts
from apps.core.src.agent.banking.tools.transfer_tools import search_beneficiaries


class UserContextLoader:
    """
    Loads user-specific context before any processing.
    This ensures typo correction and intent understanding have full context.
    """

    async def load_context(self, phone_number: str) -> UserContext:
        """
        Load all user context upfront.

        Args:
            phone_number: User's phone number

        Returns:
            UserContext with accounts, beneficiaries, and entity vocabularies
        """
        accounts_result = get_user_accounts.invoke(
            {"phone_number": phone_number})
        accounts = accounts_result.get(
            "accounts", []) if accounts_result.get("success") else []

        beneficiaries_result = search_beneficiaries.invoke({
            "phone_number": phone_number,
            "search_term": ""
        })
        beneficiaries = beneficiaries_result.get(
            "matches", []) if beneficiaries_result.get("success") else []

        # TODO:
        recent_transactions: List[Dict[str, Any]] = []

        entity_names = self._extract_entity_names(accounts, beneficiaries)

        return UserContext(
            phone_number=phone_number,
            accounts=accounts,
            beneficiaries=beneficiaries,
            recent_transactions=recent_transactions,
            entity_names=entity_names
        )

    def _extract_entity_names(
        self,
        accounts: List[Dict[str, Any]],
        beneficiaries: List[Dict[str, Any]]
    ) -> EntityVocabulary:
        """
        Build a vocabulary of valid entity names for typo correction.
        """
        beneficiary_names = [b.get("name", "")
                             for b in beneficiaries if b.get("name")]
        beneficiary_nicknames = [
            b.get("nickname", "")
            for b in beneficiaries
            if b.get("nickname")
        ]

        bank_names = list(set(
            a.get("bank_name", "")
            for a in accounts
            if a.get("bank_name")
        ))

        account_names = [
            a.get("account_name", "")
            for a in accounts
            if a.get("account_name")
        ]

        common_variations = {
            "mum": ["mummy", "mom", "mother", "mama"],
            "dad": ["daddy", "father", "papa", "baba"],
            "sis": ["sister", "sissy"],
            "bro": ["brother", "bro"],
        }

        return EntityVocabulary(
            beneficiary_names=beneficiary_names,
            beneficiary_nicknames=beneficiary_nicknames,
            bank_names=bank_names,
            account_names=account_names,
            common_variations=common_variations
        )
