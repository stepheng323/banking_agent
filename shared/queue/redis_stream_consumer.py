"""Redis Streams consumer used by ECS chat-critical worker."""

import json
import socket
from dataclasses import dataclass
from typing import Any

from shared.cache.redis_client import RedisClient
from shared.queue.contracts import resolve_contract_from_redis_stream_name


@dataclass(frozen=True, slots=True)
class RedisStreamRecord:
    """Decoded stream record with topic metadata and ack coordinates."""

    stream_name: str
    record_id: str
    topic: str
    payload: dict[str, Any]


class RedisStreamConsumer:
    """Consume chat records from Redis Streams using a consumer group."""

    def __init__(
        self,
        stream_names: list[str],
        group_name: str = "core-chat-worker",
        consumer_name: str | None = None,
    ) -> None:
        self.redis = RedisClient.get_client()
        self.stream_names = stream_names
        self.group_name = group_name
        self.consumer_name = consumer_name or socket.gethostname()
        self._groups_ready = False

    async def ensure_groups(self) -> None:
        """Create stream groups idempotently."""
        if self._groups_ready:
            return

        for stream_name in self.stream_names:
            try:
                await self.redis.xgroup_create(stream_name, self.group_name, id="$", mkstream=True)
            except Exception as exc:
                # BUSYGROUP is expected on warm workers/restarts.
                if "BUSYGROUP" not in str(exc):
                    raise
        self._groups_ready = True

    async def claim_stale(self, min_idle_ms: int = 60_000, count: int = 20) -> list[RedisStreamRecord]:
        """Claim pending idle records left by dead consumers."""
        await self.ensure_groups()
        claimed: list[RedisStreamRecord] = []

        for stream_name in self.stream_names:
            next_start = "0-0"
            response = await self.redis.xautoclaim(
                stream_name,
                self.group_name,
                self.consumer_name,
                min_idle_ms=min_idle_ms,
                start_id=next_start,
                count=count,
            )
            # redis-py returns (next_start_id, [(id, fields), ...], [deleted_ids])
            if not isinstance(response, (list, tuple)) or len(response) < 2:
                continue

            for record_id, fields in response[1]:
                topic = fields.get("topic")
                payload_raw = fields.get("payload", "{}")
                if not topic:
                    contract = resolve_contract_from_redis_stream_name(stream_name)
                    topic = contract.logical_topic if contract else ""
                payload = self._decode_payload(payload_raw)
                claimed.append(
                    RedisStreamRecord(
                        stream_name=stream_name,
                        record_id=record_id,
                        topic=topic,
                        payload=payload,
                    )
                )

        return claimed

    async def consume(self, count: int = 20, block_ms: int = 5000) -> list[RedisStreamRecord]:
        """Consume new records from all configured streams."""
        await self.ensure_groups()
        streams_cursor = dict.fromkeys(self.stream_names, ">")
        response = await self.redis.xreadgroup(
            groupname=self.group_name,
            consumername=self.consumer_name,
            streams=streams_cursor,
            count=count,
            block=block_ms,
        )

        records: list[RedisStreamRecord] = []
        for stream_name, stream_records in response:
            for record_id, fields in stream_records:
                topic = fields.get("topic")
                payload_raw = fields.get("payload", "{}")
                if not topic:
                    contract = resolve_contract_from_redis_stream_name(stream_name)
                    topic = contract.logical_topic if contract else ""
                payload = self._decode_payload(payload_raw)
                records.append(
                    RedisStreamRecord(
                        stream_name=stream_name,
                        record_id=record_id,
                        topic=topic,
                        payload=payload,
                    )
                )
        return records

    async def ack(self, stream_name: str, record_id: str) -> None:
        """Acknowledge a processed record."""
        await self.redis.xack(stream_name, self.group_name, record_id)

    @staticmethod
    def _decode_payload(payload_raw: str) -> dict[str, Any]:
        try:
            decoded = json.loads(payload_raw)
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {}
