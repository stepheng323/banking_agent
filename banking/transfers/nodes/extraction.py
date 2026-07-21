# ruff: noqa: E501
"""Extraction logic."""

from typing import Any

from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.shared.account_selection.reference import (
    build_source_account_patch,
    match_source_account_reference,
)
from banking.transactions.shared.confirmation.classifier import classify_confirmation_reply_sync
from banking.transactions.shared.extraction_utils import try_extract_numeric_index
from banking.transactions.shared.scheduling import (
    SCHEDULE_FIELD_NAMES,
    parse_schedule_slot_patch,
    schedule_required_prompt,
)
from banking.transfers.extraction.parsers import (
    parse_account_and_bank_input,
    parse_amount_input,
    parse_bank_name_slot_reply,
    parse_recipient_name_slot_reply,
    parse_simple_transfer_command,
    parse_single_confirmation_transfer_edit,
    recipient_schedule_cleanup_patch,
    should_override_skip_extraction,
)
from banking.transfers.extraction.selection import (
    build_resolved_referent_patch,
    render_beneficiary_retry_prompt,
    render_referent_recipient_retry_prompt,
    resolve_beneficiary_selection_from_input,
    resolve_referent_recipient_selection_from_input,
)
from banking.transfers.extraction.updates import extract_transfer_update
from banking.transfers.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from banking.transfers.pipeline.base import TransferStep
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ExtractionStep(TransferStep):
    """Refines transfer data from user message."""

    def __init__(self, user_message: str | None):
        self.user_message = user_message

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        if not self.user_message:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        def _with_skip_patch(patch: dict[str, Any] | None = None) -> dict[str, Any]:
            cleanup_patch = recipient_schedule_cleanup_patch(data)
            if not data.skip_extraction and not data.confirmation_message_scoped and not cleanup_patch:
                return dict(patch or {})
            merged = dict(patch or {})
            for key, value in cleanup_patch.items():
                merged.setdefault(key, value)
            if data.skip_extraction:
                merged.setdefault("skip_extraction", False)
            if data.confirmation_message_scoped:
                merged.setdefault("confirmation_message_scoped", False)
            return merged

        raw_required_fields = getattr(worker_context, "required_fields", [])
        required_fields = raw_required_fields if isinstance(raw_required_fields, list) else []
        schedule_required_fields = [field for field in required_fields if field in SCHEDULE_FIELD_NAMES]
        if schedule_required_fields:
            schedule_patch, remaining_schedule_fields = parse_schedule_slot_patch(
                self.user_message,
                schedule_required_fields,
            )
            if schedule_patch:
                logger.info(
                    "deterministic_schedule_slot_fastpath",
                    fields=sorted(key for key in schedule_patch if key != "confirmation"),
                )
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(schedule_patch),
                )
            if len(schedule_required_fields) == len(required_fields):
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=remaining_schedule_fields or schedule_required_fields,
                    prompt=schedule_required_prompt(
                        remaining_schedule_fields or schedule_required_fields, context.language
                    ),
                    patch=_with_skip_patch({}),
                )
        if (
            gates.confirmation_confirmed
            and data.confirmation.confirmed
            and not required_fields
            and data.recipient_account
            and (data.source_account_id or data.source_bank_name)
        ):
            logger.info("skip_transfer_extraction_confirmed_resume")
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        awaiting_amount = "amount" in required_fields
        if awaiting_amount and data.suggested_amount:
            reply = self.user_message.strip()
            if reply == "1":
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(
                        {
                            "amount": float(data.suggested_amount),
                            "suggested_amount": None,
                            "confirmation": {"confirmed": False},
                        }
                    ),
                )
            if reply == "2":
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(
                        {
                            "suggested_amount": None,
                            "confirmation": {"confirmed": False},
                        }
                    ),
                )
            confirmation_decision = classify_confirmation_reply_sync(
                self.user_message,
                prompt_kind="amount_suggestion",
                locale=context.language,
            )
            if confirmation_decision.is_approval:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(
                        {
                            "amount": float(data.suggested_amount),
                            "suggested_amount": None,
                            "confirmation": {"confirmed": False},
                        }
                    ),
                )
            if confirmation_decision.is_rejection:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(
                        {
                            "suggested_amount": None,
                            "confirmation": {"confirmed": False},
                        }
                    ),
                )

        if awaiting_amount:
            parsed_amount = parse_amount_input(self.user_message)
            if parsed_amount is not None:
                logger.info("deterministic_amount_fastpath", amount=parsed_amount)
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(
                        {
                            "amount": parsed_amount,
                            "suggested_amount": None,
                            "confirmation": {"confirmed": False},
                        }
                    ),
                )

        confirmation_edit_patch = parse_single_confirmation_transfer_edit(
            self.user_message,
            data,
            context,
            worker_context,
        )
        if confirmation_edit_patch is not None:
            logger.info(
                "deterministic_confirmation_transfer_edit_fastpath",
                fields=sorted(confirmation_edit_patch.keys()),
            )
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(confirmation_edit_patch),
            )

        referent_patch = build_resolved_referent_patch(data, context)
        if referent_patch:
            logger.info(
                "deterministic_transfer_referent_fastpath",
                fields=sorted(referent_patch.keys()),
            )
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(referent_patch),
            )

        # Optimization: Phase 4 (Planner-as-Extractor)
        # Skip extraction if Planner already did it (signaled by flag)
        override_skip_extraction = data.skip_extraction and should_override_skip_extraction(data)
        if data.skip_extraction and not override_skip_extraction:
            logger.info("skip_redundant_extraction", task="transfer")
            return TransactionResult(outcome=TransactionOutcome.OK, patch={"skip_extraction": False})
        if override_skip_extraction:
            logger.info("override_skip_extraction_for_account_like_recipient", recipient=data.recipient_name)

        waiting_for_beneficiary = "beneficiary_id" in required_fields
        waiting_for_referent_recipient = "referent_recipient_id" in required_fields
        waiting_for_source_account = "source_account_id" in required_fields
        waiting_for_recipient_bank_only = set(required_fields) == {"recipient_bank_name"}
        waiting_for_recipient_identity = (
            not data.recipient_name
            and not waiting_for_beneficiary
            and not waiting_for_referent_recipient
            and not waiting_for_source_account
            and "recipient_account" in required_fields
            and "recipient_bank_name" in required_fields
        )
        if waiting_for_recipient_bank_only:
            bank_name = parse_bank_name_slot_reply(self.user_message)
            if bank_name:
                logger.info("deterministic_recipient_bank_slot_fastpath", bank=bank_name)
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(
                        {
                            "recipient_bank_name": bank_name,
                            "recipient_bank_code": None,
                            "recipient_bank_code_provider": None,
                            "recipient_resolution_provider": None,
                            "recipient_resolution_mode": None,
                            "recipient_resolved_name": None,
                            "confirmation": {"confirmed": False},
                        }
                    ),
                )
        if waiting_for_recipient_identity:
            recipient_name = parse_recipient_name_slot_reply(self.user_message)
            if recipient_name:
                logger.info("deterministic_recipient_name_slot_fastpath", recipient=recipient_name)
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(
                        {
                            "recipient_name": recipient_name,
                            "recipient_resolved_name": None,
                            "beneficiary_id": None,
                            "beneficiary_candidates": [],
                            "confirmation": {"confirmed": False},
                        }
                    ),
                )
        if waiting_for_referent_recipient:
            selection_referent_patch, invalid_referents = resolve_referent_recipient_selection_from_input(
                self.user_message,
                data.referent_recipient_candidates,
            )
            if selection_referent_patch:
                logger.info(
                    "transfer_extraction_referent_recipient_selection",
                    fields=sorted(selection_referent_patch.keys()),
                )
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(selection_referent_patch),
                )
            if invalid_referents:
                retry_prompt = render_referent_recipient_retry_prompt(invalid_referents, context.language)
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["referent_recipient_id"],
                    prompt=retry_prompt,
                    patch=_with_skip_patch({"referent_recipient_candidates": invalid_referents}),
                    details={
                        "ambiguity": "MULTIPLE_REFERENT_RECIPIENTS",
                        "candidates": invalid_referents,
                        "options": [
                            {
                                "id": str(candidate.get("option_id", "")).strip(),
                                "title": str(candidate.get("label", "")),
                            }
                            for candidate in invalid_referents
                            if str(candidate.get("option_id", "")).strip()
                        ],
                    },
                )
        if waiting_for_beneficiary:
            beneficiary_patch, invalid_candidates = resolve_beneficiary_selection_from_input(
                self.user_message,
                data.beneficiary_candidates,
                data.recipient_name,
                context.beneficiaries,
            )
            if beneficiary_patch:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(beneficiary_patch),
                )
            if invalid_candidates:
                retry_prompt = render_beneficiary_retry_prompt(
                    recipient_name=data.recipient_name,
                    candidates=invalid_candidates,
                    locale=context.language,
                )
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["beneficiary_id"],
                    prompt=retry_prompt,
                    patch=_with_skip_patch({"beneficiary_candidates": invalid_candidates}),
                    details={
                        "ambiguity": "MULTIPLE_BENEFICIARIES",
                        "candidates": invalid_candidates,
                        "options": [
                            {
                                "id": str(candidate.get("option_id", "")).strip(),
                                "title": str(candidate.get("label", "")),
                            }
                            for candidate in invalid_candidates
                            if str(candidate.get("option_id", "")).strip()
                        ],
                    },
                )
            # A monetary reply belongs to a sibling task in a unified mixed
            # interrupt; it is never a beneficiary ordinal.  Keep the
            # selection open and do not let the broad extractor invent a
            # recipient binding from an amount such as "2k".
            if parse_amount_input(self.user_message) is not None:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["beneficiary_id"],
                    prompt=render_beneficiary_retry_prompt(
                        recipient_name=data.recipient_name,
                        candidates=data.beneficiary_candidates,
                        locale=context.language,
                    ),
                    patch=_with_skip_patch({"beneficiary_candidates": data.beneficiary_candidates}),
                    details={
                        "ambiguity": "MULTIPLE_BENEFICIARIES",
                        "candidates": data.beneficiary_candidates,
                    },
                )

        # [DETERMINISTIC FALLBACK] Numeric index selection
        # If user replies with "1" or "2" while selecting source account, map directly.
        source_account = None
        if waiting_for_source_account:
            source_account = match_source_account_reference(self.user_message, context.accounts)
        numeric_patch = None
        if waiting_for_source_account and not (
            self.user_message.strip().isdigit() and len(self.user_message.strip()) >= 4
        ):
            numeric_patch = try_extract_numeric_index(self.user_message, "transfer")
        if numeric_patch:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(numeric_patch),
            )
        if source_account:
            logger.info(
                "deterministic_source_account_reference_fastpath",
                bank=source_account.get("bank_name"),
                account_id=source_account.get("id"),
            )
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(build_source_account_patch(source_account)),
            )

        # [DETERMINISTIC FAST-PATH] Account number + bank name
        # When user replies with e.g. "8067892221 Opay" and we're waiting for account+bank,
        # parse deterministically instead of relying on the LLM.
        awaiting_account_and_bank = "recipient_account" in required_fields and "recipient_bank_name" in required_fields
        parsed_account_and_bank = parse_account_and_bank_input(self.user_message) if self.user_message else None
        if parsed_account_and_bank and (
            awaiting_account_and_bank or not (data.recipient_account or data.recipient_bank_name)
        ):
            acct, bank = parsed_account_and_bank
            if awaiting_account_and_bank:
                logger.info(
                    "deterministic_account_bank_fastpath",
                    account=acct,
                    bank=bank,
                )
            else:
                logger.info(
                    "deterministic_initial_account_bank_fastpath",
                    account=acct,
                    bank=bank,
                )
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(
                    {
                        "recipient_account": acct,
                        "recipient_bank_name": bank,
                        "recipient_bank_code": None,
                        "recipient_bank_code_provider": None,
                        "recipient_resolution_provider": None,
                        "recipient_resolution_mode": None,
                        "recipient_resolved_name": None,
                        "name_mismatch": False,
                        "name_match_score": None,
                        "name_mismatch_warning": None,
                        **({"amount_suggestion_disabled": True} if not awaiting_account_and_bank else {}),
                        "confirmation": {"confirmed": False},
                    }
                ),
            )

        simple_transfer_patch = parse_simple_transfer_command(
            self.user_message,
            data,
            context.beneficiaries,
        )
        if simple_transfer_patch is not None:
            logger.info(
                "deterministic_simple_transfer_fastpath",
                amount=simple_transfer_patch.get("amount"),
                has_recipient_name=bool(simple_transfer_patch.get("recipient_name")),
                has_recipient_account=bool(simple_transfer_patch.get("recipient_account")),
            )
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(simple_transfer_patch),
            )

        if not worker_context.extractor:
            logger.info("transfer_extraction_skipped", reason="extractor_unavailable")
            patch = _with_skip_patch({})
            return TransactionResult(outcome=TransactionOutcome.OK, patch=patch)

        res = await extract_transfer_update(
            data,
            worker_context.extractor,
            self.user_message,
            {
                "phone_number": context.phone_number,
                "language": context.language,
                "beneficiaries": context.beneficiaries,
                "accounts": context.accounts,
                "required_fields": worker_context.required_fields,
                "previousResponse": worker_context.previous_response,
                "known_recipient": {
                    "recipient_name": data.recipient_name,
                    "recipient_resolved_name": data.recipient_resolved_name,
                    "recipient_account": data.recipient_account,
                    "recipient_bank_name": data.recipient_bank_name,
                },
            },
        )

        if waiting_for_beneficiary and res.patch:
            # LLM fallback strategy: if LLM extracted an index while we are waiting for a beneficiary, apply it.
            index_val = res.patch.pop("recipient_binding_index", None) or res.patch.pop("source_account_index", None)
            if isinstance(index_val, int) and 1 <= index_val <= len(data.beneficiary_candidates):
                candidate = data.beneficiary_candidates[index_val - 1]
                beneficiary_id = str(candidate.get("beneficiary_id", "")).strip()
                if beneficiary_id:
                    res.patch["beneficiary_id"] = beneficiary_id
                    # Apply full candidate details to the patch so Resolver's matches_selected_beneficiary safety check succeeds
                    fields = [
                        "recipient_name", "recipient_resolved_name", "recipient_account",
                        "recipient_bank_name", "recipient_bank_code", "recipient_bank_code_provider",
                        "recipient_resolution_provider", "recipient_resolution_mode", "resolved_from_saved_beneficiary"
                    ]
                    for field in fields:
                        if candidate.get(field) not in (None, ""):
                            res.patch[field] = candidate.get(field)

        res.patch = _with_skip_patch(res.patch)

        return res
