"""Parallel Redis/context loader for ContextManager."""

import json
import time
from typing import Any, Literal

import shared.services.context_redis_state as context_redis_state
from shared.cache.redis_client import RedisClient
from shared.services.context_user_data import hydrate_user_context_from_cache_snapshot
from shared.utils.logging import log_fingerprint


async def load_context_parallel(
    phone_number: str,
    *,
    path_label: str = "planner_path",
    user: Any | None = None,
    profile_mode: Literal["full", "minimal"] = "full",
    account_mode: Literal["full", "cache_only"] = "full",
    beneficiary_mode: Literal["full", "cache_only"] = "full",
    user_repo: Any | None,
    account_repo: Any | None,
    beneficiary_repo: Any | None,
    data_cache: Any,
    logger: Any,
    log_latency_span: Any,
    load_user_context_fallback: Any,
    get_user_language: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None, str | None, str | None]:
    try:
        redis_client = RedisClient.get_client()
        keys = [
            f"user:{phone_number}:conversation_state",
            f"user:{phone_number}:last_response",
            f"user:{phone_number}:beneficiary_suggestion",
            f"user:{phone_number}:language",
            f"user:{phone_number}:chat_history",
            f"cache:user:profile:{phone_number}",
            f"cache:user:accounts:{phone_number}",
            f"cache:user:beneficiaries:{phone_number}",
            f"cache:user:snapshot:{phone_number}",
        ]

        cache_fetch_start = time.perf_counter()
        pipe = redis_client.pipeline()
        for key in keys[0:4]:
            pipe.get(key)

        pipe.lrange(keys[4], -10, -1)
        for key in keys[5:9]:
            pipe.get(key)

        results = await pipe.execute()
        log_latency_span(
            span="context_cache_fetch",
            duration_ms=(time.perf_counter() - cache_fetch_start) * 1000,
            phone_number=phone_number,
            path_label=path_label,
        )

        cached_user_data, snapshot_backfill = data_cache.decode_user_data_fields(
            profile_raw=results[5],
            accounts_raw=results[6],
            beneficiaries_raw=results[7],
            snapshot_raw=results[8],
        )
        if any(snapshot_backfill.values()):
            logger.info(
                "context_user_data_snapshot_backfill",
                phone=phone_number,
                profile=snapshot_backfill["profile"],
                accounts=snapshot_backfill["accounts"],
                beneficiaries=snapshot_backfill["beneficiaries"],
            )
            await data_cache.set_user_data_snapshot(
                phone_number,
                profile=cached_user_data["profile"] if isinstance(cached_user_data["profile"], dict) else None,
                cache_profile=snapshot_backfill["profile"],
                accounts=cached_user_data["accounts"] if isinstance(cached_user_data["accounts"], list) else None,
                cache_accounts=snapshot_backfill["accounts"],
                beneficiaries=(
                    cached_user_data["beneficiaries"] if isinstance(cached_user_data["beneficiaries"], list) else None
                ),
                cache_beneficiaries=snapshot_backfill["beneficiaries"],
                refresh_snapshot=False,
            )

        if (
            cached_user_data.get("profile") is None
            and cached_user_data.get("accounts") is None
            and cached_user_data.get("beneficiaries") is None
            and any(results[index] for index in range(0, 5))
        ):
            logger.info(
                "context_user_data_restart_gap",
                phone_hash=log_fingerprint(phone_number),
                has_conversation_state=bool(results[0]),
                has_last_response=bool(results[1]),
                has_beneficiary_suggestion=bool(results[2]),
                has_language=bool(results[3]),
                has_history=bool(results[4]),
            )
        user_ctx = await hydrate_user_context_from_cache_snapshot(
            phone_number,
            user=user,
            cached_data=cached_user_data,
            path_label=path_label,
            profile_mode=profile_mode,
            account_mode=account_mode,
            beneficiary_mode=beneficiary_mode,
            user_repo=user_repo,
            account_repo=account_repo,
            beneficiary_repo=beneficiary_repo,
            data_cache=data_cache,
            logger=logger,
            log_latency_span=log_latency_span,
        )

        conversation_state = None
        if results[0]:
            try:
                conversation_state = json.loads(results[0])
            except (json.JSONDecodeError, TypeError):
                pass

        last_response = results[1] if results[1] else None
        suggestion_data = results[2] if results[2] else None
        language = results[3] if results[3] else None

        history_raw = results[4] if results[4] else []
        history = []
        try:
            history = [json.loads(item) for item in history_raw]
        except Exception:
            pass

        user_ctx["language"] = language
        user_ctx["detected_language"] = language
        user_ctx["history"] = history

        return user_ctx, conversation_state, last_response, suggestion_data

    except Exception as e:
        logger.error(
            "error_in_parallel_context",
            phone_hash=log_fingerprint(phone_number),
            error_type=type(e).__name__,
        )
        user_ctx = await load_user_context_fallback(
            phone_number,
            user=user,
            path_label=path_label,
            profile_mode=profile_mode,
            beneficiary_mode=beneficiary_mode,
        )
        conversation_state = await context_redis_state.get_conversation_state(phone_number)
        last_response = await context_redis_state.get_last_response(phone_number)
        language = await get_user_language(phone_number)
        history = await context_redis_state.get_conversation_history(phone_number)

        try:
            redis_client = RedisClient.get_client()
            suggestion_key = f"user:{phone_number}:beneficiary_suggestion"
            suggestion_data = await redis_client.get(suggestion_key)
        except Exception:
            suggestion_data = None

        user_ctx["language"] = language
        user_ctx["detected_language"] = language
        user_ctx["history"] = history

        return user_ctx, conversation_state, last_response, suggestion_data
