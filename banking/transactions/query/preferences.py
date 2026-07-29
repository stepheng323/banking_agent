"""Persistence and deterministic application of query preferences."""

from __future__ import annotations

from banking.persistence.unit_of_work import UnitOfWork
from shared.cache.user_data import UserDataCache
from shared.types.query_preferences import QueryPreferencesV1, QueryPreferenceUpdate
from shared.utils.logging import get_logger

logger = get_logger(__name__)
_PREFERENCE_KEY = "query_preferences"


class QueryPreferenceAccountError(ValueError):
    """A requested default account could not be resolved uniquely."""


def _account_value(account: object, key: str) -> object:
    return account.get(key) if isinstance(account, dict) else getattr(account, key, None)


def _normalized(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _resolve_account_refs(names: list[str], accounts: list[object]) -> list[str]:
    resolved: list[str] = []
    for name in names:
        target = _normalized(name)
        matches = [
            account
            for account in accounts
            if target
            and target
            in {
                _normalized(_account_value(account, "bank_name")),
                _normalized(_account_value(account, "bank")),
                _normalized(_account_value(account, "display_name")),
            }
        ]
        if len(matches) != 1:
            raise QueryPreferenceAccountError("default account preference requires one linked-account match")
        stable_id = (
            _account_value(matches[0], "account_id")
            or _account_value(matches[0], "mono_account_id")
            or _account_value(matches[0], "id")
        )
        if not stable_id:
            raise QueryPreferenceAccountError("linked account has no stable reference")
        ref = str(stable_id)
        if ref not in resolved:
            resolved.append(ref)
    return resolved


def apply_query_preference_update(
    current: QueryPreferencesV1,
    update: QueryPreferenceUpdate,
    *,
    accounts: list[object],
) -> QueryPreferencesV1:
    """Apply one explicit sparse update without inferring semantic defaults."""

    if update.reset_all:
        return QueryPreferencesV1()

    values = current.model_dump(mode="python")
    for field in update.clear_fields:
        values[field] = [] if field == "default_account_refs" else None

    for field in (
        "presentation_detail",
        "default_shape",
        "relative_period_mode",
        "default_activity_measure",
        "default_status_inclusion",
    ):
        value = getattr(update, field)
        if value is not None:
            values[field] = value

    if update.default_account_names is not None:
        values["default_account_refs"] = _resolve_account_refs(update.default_account_names, accounts)

    return QueryPreferencesV1.model_validate(values)


async def persist_query_preferences(
    *,
    user_id: str,
    phone_number: str | None,
    update: QueryPreferenceUpdate,
    accounts: list[object],
) -> QueryPreferencesV1:
    """Lock, update, and commit the user's query preferences."""

    async with UnitOfWork() as uow:
        user = await uow.users.get_by_id_for_update(user_id)
        if user is None:
            raise ValueError("user not found")
        extra_data = dict(user.extra_data or {})
        raw_current = extra_data.get(_PREFERENCE_KEY)
        try:
            current = (
                QueryPreferencesV1.model_validate(raw_current)
                if isinstance(raw_current, dict)
                else QueryPreferencesV1()
            )
        except Exception:
            current = QueryPreferencesV1()
        updated = apply_query_preference_update(current, update, accounts=accounts)
        extra_data[_PREFERENCE_KEY] = updated.model_dump(mode="json")
        user.extra_data = extra_data
        await uow.db.flush()

    if phone_number:
        try:
            await UserDataCache().invalidate_user_profile(phone_number)
        except Exception:
            logger.warning("query_preferences_cache_invalidation_failed")
    logger.info(
        "query_preferences_updated",
        changed_fields=sorted(
            {
                *update.model_fields_set,
                *update.clear_fields,
            }
            - {"clear_fields"}
        ),
        reset_all=update.reset_all,
        account_scope_count=len(updated.default_account_refs),
    )
    return updated


__all__ = [
    "QueryPreferenceAccountError",
    "apply_query_preference_update",
    "persist_query_preferences",
]
