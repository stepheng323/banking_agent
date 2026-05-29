"""User data cache hydration for ContextManager."""

import asyncio
import time
from collections.abc import Awaitable
from typing import Any, Literal

from shared.utils.logging import log_fingerprint
from shared.utils.serialization import sqlalchemy_to_dict


def serialize_profile(profile_obj: Any) -> tuple[dict[str, Any] | None, str | None]:
    if isinstance(profile_obj, dict):
        raw_user_id = profile_obj.get("id")
        user_id = str(raw_user_id) if raw_user_id else None
        return dict(profile_obj), user_id
    if profile_obj is not None:
        return sqlalchemy_to_dict(profile_obj), str(profile_obj.id)
    return None, None


def serialize_rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) if isinstance(row, dict) else sqlalchemy_to_dict(row) for row in rows]


async def hydrate_user_context_from_cache_snapshot(
    phone_number: str,
    *,
    user: Any | None = None,
    cached_data: dict[str, Any],
    path_label: str,
    profile_mode: Literal["full", "minimal"] = "full",
    account_mode: Literal["full", "cache_only"] = "full",
    beneficiary_mode: Literal["full", "cache_only"] = "full",
    user_repo: Any | None,
    account_repo: Any | None,
    beneficiary_repo: Any | None,
    data_cache: Any,
    logger: Any,
    log_latency_span: Any,
) -> dict[str, Any]:
    cache_profile = cached_data.get("profile")
    cache_accounts = cached_data.get("accounts")
    cache_beneficiaries = cached_data.get("beneficiaries")

    cache_status = "miss"
    cache_hits = sum(v is not None for v in (cache_profile, cache_accounts, cache_beneficiaries))
    if cache_hits == 3:
        cache_status = "hit"
    elif cache_hits > 0:
        cache_status = "partial_hit"
    logger.info("context_user_data_cache", phone_hash=log_fingerprint(phone_number), status=cache_status)

    if cache_hits == 3:
        return {
            "profile": cache_profile,
            "accounts": cache_accounts,
            "beneficiaries": cache_beneficiaries,
        }

    profile_obj = user if user is not None else cache_profile
    needs_accounts_fetch = cache_accounts is None and account_mode == "full"
    needs_beneficiaries_fetch = cache_beneficiaries is None and beneficiary_mode == "full"
    needs_profile_for_collection_fetch = profile_obj is None and (needs_accounts_fetch or needs_beneficiaries_fetch)
    if profile_obj is None and user_repo and (profile_mode == "full" or needs_profile_for_collection_fetch):
        profile_start = time.perf_counter()
        profile_obj = await user_repo.get_by_phone(phone_number)
        log_latency_span(
            span="context_profile_fetch",
            duration_ms=(time.perf_counter() - profile_start) * 1000,
            phone_number=phone_number,
            path_label=path_label,
        )

    safe_profile, user_id = serialize_profile(profile_obj)

    async def _timed_fetch(label: str, op: Any) -> tuple[str, Any]:
        fetch_start = time.perf_counter()
        result = await op
        log_latency_span(
            span=f"context_{label}_fetch",
            duration_ms=(time.perf_counter() - fetch_start) * 1000,
            phone_number=phone_number,
            path_label=path_label,
        )
        return label, result

    accounts: list[Any] = list(cache_accounts) if cache_accounts is not None else []
    beneficiaries: list[Any] = list(cache_beneficiaries) if cache_beneficiaries is not None else []

    fetch_ops: list[tuple[str, Awaitable[tuple[str, Any]]]] = []
    if user_id and cache_accounts is None and account_mode == "full" and account_repo:
        fetch_ops.append(("accounts", _timed_fetch("accounts", account_repo.get_by_user(user_id))))
    if user_id and cache_beneficiaries is None and beneficiary_mode == "full" and beneficiary_repo:
        fetch_ops.append(("beneficiaries", _timed_fetch("beneficiaries", beneficiary_repo.get_by_user(user_id))))

    if fetch_ops:
        labels = [label for label, _ in fetch_ops]
        results = await asyncio.gather(*[op for _, op in fetch_ops], return_exceptions=True)
        for label, result in zip(labels, results, strict=False):
            if isinstance(result, BaseException):
                logger.warning(
                    "context_user_data_fetch_error",
                    phone=phone_number,
                    field=label,
                    error=str(result),
                )
                continue
            _, value = result
            if label == "accounts":
                accounts = list(value)
            else:
                beneficiaries = list(value)

    safe_accounts = serialize_rows(accounts)
    safe_beneficiaries = serialize_rows(beneficiaries)

    cache_profile_write = safe_profile is not None and cache_profile is None
    cache_accounts_write = bool(safe_accounts) and cache_accounts is None
    cache_beneficiaries_write = cache_beneficiaries is None and beneficiary_mode == "full"
    if cache_profile_write or cache_accounts_write or cache_beneficiaries_write:
        write_start = time.perf_counter()
        await data_cache.set_user_data_snapshot(
            phone_number,
            profile=safe_profile,
            cache_profile=cache_profile_write,
            accounts=safe_accounts,
            cache_accounts=cache_accounts_write,
            beneficiaries=safe_beneficiaries,
            cache_beneficiaries=cache_beneficiaries_write,
        )
        log_latency_span(
            span="context_cache_write",
            duration_ms=(time.perf_counter() - write_start) * 1000,
            phone_number=phone_number,
            path_label=path_label,
        )

    return {
        "profile": safe_profile,
        "accounts": safe_accounts,
        "beneficiaries": safe_beneficiaries,
    }
