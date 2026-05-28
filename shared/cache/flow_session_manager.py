"""Global flow session management for all transaction types."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from shared.cache.redis_client import RedisClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_SESSION_TTL = 3600  # 1 hour


@dataclass(slots=True)
class SessionReadResult:
    """Detailed session read result for strict session integrity checks."""

    status: Literal["found", "missing", "backend_error"]
    data: dict[str, Any] | None = None
    error: str | None = None

    @property
    def found(self) -> bool:
        return self.status == "found" and self.data is not None

    @property
    def missing(self) -> bool:
        return self.status == "missing"

    @property
    def backend_error(self) -> bool:
        return self.status == "backend_error"


class FlowSessionManager:
    """Manages flow session data in Redis."""

    def __init__(
        self,
        redis: Any = None,
        key_prefix: str = "flow",
        ttl: int = DEFAULT_SESSION_TTL,
    ):
        self.redis: Any = redis or RedisClient.get_client()
        self.key_prefix = key_prefix
        self.ttl = ttl

    def _session_key(self, flow_token: str) -> str:
        return f"{self.key_prefix}:{flow_token}"

    @staticmethod
    def _token_fingerprint(flow_token: str | None) -> str:
        if not flow_token:
            return ""
        return hashlib.sha256(str(flow_token).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _mask_phone(phone_number: str | None) -> str:
        if not phone_number:
            return ""
        normalized = str(phone_number).strip()
        if len(normalized) <= 4:
            return normalized
        return f"{normalized[:4]}***{normalized[-2:]}"

    async def read_session(self, flow_token: str) -> SessionReadResult:
        """Read session data with explicit status reporting."""
        try:
            data = await self.redis.get(self._session_key(flow_token))
            if data:
                session = json.loads(data)
                phone_number = ""
                step = None
                if isinstance(session, dict):
                    phone_number = str(session.get("phone_number") or "")
                    step = session.get("step")
                logger.info(
                    "flow_session_read",
                    flow_token_hash=self._token_fingerprint(flow_token),
                    status="found",
                    step=step,
                    phone=self._mask_phone(phone_number),
                )
                return SessionReadResult(status="found", data=session)
            logger.info("flow_session_read", flow_token_hash=self._token_fingerprint(flow_token), status="missing")
        except Exception as e:
            logger.error("flow_session_read_failed", flow_token_hash=self._token_fingerprint(flow_token), error=str(e))
            return SessionReadResult(status="backend_error", error=str(e))
        return SessionReadResult(status="missing")

    async def update_session_strict(self, flow_token: str, updates: dict[str, Any], *, verify: bool = False) -> bool:
        """Merge updates into existing session with optional read-back verification."""
        try:
            existing_result = await self.read_session(flow_token)
            if existing_result.backend_error:
                logger.error(
                    "flow_session_store_failed",
                    flow_token_hash=self._token_fingerprint(flow_token),
                    reason="read_existing_failed",
                    error=existing_result.error,
                )
                return False

            existing = existing_result.data or {}
            existing.update(updates)
            await self.redis.set(self._session_key(flow_token), json.dumps(existing), ex=self.ttl)
            logger.info(
                "flow_session_created",
                flow_token_hash=self._token_fingerprint(flow_token),
                step=existing.get("step"),
                phone=self._mask_phone(str(existing.get("phone_number") or "")),
            )
        except Exception as e:
            logger.error(
                "flow_session_store_failed",
                flow_token_hash=self._token_fingerprint(flow_token),
                reason="write_failed",
                error=str(e),
            )
            return False

        if not verify:
            return True

        verify_result = await self.read_session(flow_token)
        if not verify_result.found:
            logger.error(
                "flow_session_verify_failed",
                flow_token_hash=self._token_fingerprint(flow_token),
                reason=verify_result.status,
                error=verify_result.error,
            )
            return False

        stored = verify_result.data or {}
        for key, value in updates.items():
            if stored.get(key) != value:
                logger.error(
                    "flow_session_verify_failed",
                    flow_token_hash=self._token_fingerprint(flow_token),
                    reason="payload_mismatch",
                    field=key,
                )
                return False

        logger.info(
            "flow_session_verified",
            flow_token_hash=self._token_fingerprint(flow_token),
            step=stored.get("step"),
            phone=self._mask_phone(str(stored.get("phone_number") or "")),
        )
        return True

    async def delete_session(self, flow_token: str) -> None:
        """Delete session from Redis."""
        try:
            await self.redis.delete(self._session_key(flow_token))
        except Exception as e:
            logger.error("delete_session_error", error=str(e))
