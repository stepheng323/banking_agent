import json

import apps.chat.src.agent.orchestrator.context.context_redis_state as context_redis_state


class _RedisStateStub:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.expirations: dict[str, int] = {}
        self.deleted: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)
        self.lists.pop(key, None)

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        values = self.lists.get(key, [])
        end = None if stop == -1 else stop + 1
        return values[start:end]

    async def rpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).append(value)

    async def ltrim(self, key: str, start: int, stop: int) -> None:
        values = self.lists.get(key, [])
        end = None if stop == -1 else stop + 1
        self.lists[key] = values[start:end]

    async def expire(self, key: str, ttl: int) -> None:
        self.expirations[key] = ttl

    async def incr(self, key: str) -> int:
        value = int(self.values.get(key) or "0") + 1
        self.values[key] = str(value)
        return value


def _install_redis(monkeypatch) -> _RedisStateStub:
    redis_stub = _RedisStateStub()
    monkeypatch.setattr(context_redis_state.RedisClient, "get_client", staticmethod(lambda: redis_stub))
    return redis_stub


async def test_context_redis_state_round_trips_conversation_and_message_state(monkeypatch) -> None:
    redis_stub = _install_redis(monkeypatch)
    phone_number = "2348000000100"
    redis_stub.values[f"user:{phone_number}:conversation_state"] = json.dumps({"flow": "transfer"})

    assert await context_redis_state.get_conversation_state(phone_number) == {"flow": "transfer"}

    await context_redis_state.clear_conversation_state(phone_number)
    assert await context_redis_state.get_conversation_state(phone_number) is None

    await context_redis_state.save_last_response(phone_number, "Done")
    assert await context_redis_state.get_last_response(phone_number) == "Done"
    assert redis_stub.expirations[f"user:{phone_number}:last_response"] == 3600

    await context_redis_state.save_message_id(phone_number, "wamid.1")
    assert await context_redis_state.get_message_id(phone_number) == "wamid.1"
    assert redis_stub.expirations[f"user:{phone_number}:current_message_id"] == 300

    await context_redis_state.add_conversation_turn(
        phone_number,
        "assistant",
        "Transfer completed",
        metadata={"topic": " transfer ", "empty": None},
    )

    history = await context_redis_state.get_conversation_history(phone_number)
    assert history == [
        {
            "role": "assistant",
            "content": "Transfer completed",
            "metadata": {"topic": " transfer "},
            "topic": "transfer",
        }
    ]
    assert redis_stub.expirations[f"user:{phone_number}:chat_history"] == 86400


async def test_context_redis_state_reads_three_history_turns_but_retains_fifty(monkeypatch) -> None:
    redis_stub = _install_redis(monkeypatch)
    phone_number = "2348000000100"

    for index in range(55):
        await context_redis_state.add_conversation_turn(phone_number, "user", f"turn {index}")

    key = f"user:{phone_number}:chat_history"
    assert len(redis_stub.lists[key]) == 50

    history = await context_redis_state.get_conversation_history(phone_number)

    assert [item["content"] for item in history] == ["turn 52", "turn 53", "turn 54"]


async def test_context_redis_state_claims_and_releases_inbound_message(monkeypatch) -> None:
    redis_stub = _install_redis(monkeypatch)
    phone_number = "2348000000100"

    assert await context_redis_state.claim_inbound_message(phone_number, "wamid.1", ttl_seconds=30) is True
    assert await context_redis_state.claim_inbound_message(phone_number, "wamid.1", ttl_seconds=30) is False
    assert redis_stub.expirations[f"user:{phone_number}:inbound_message:wamid.1"] == 30

    await context_redis_state.release_inbound_message_claim(phone_number, "wamid.1")
    assert await context_redis_state.claim_inbound_message(phone_number, "wamid.1", ttl_seconds=30) is True
    assert await context_redis_state.claim_inbound_message(phone_number, "unknown") is True


async def test_context_redis_state_tracks_mandate_warning_count(monkeypatch) -> None:
    redis_stub = _install_redis(monkeypatch)
    phone_number = "2348000000100"

    assert await context_redis_state.get_mandate_warning_count(phone_number) == 0
    assert await context_redis_state.increment_mandate_warning_count(phone_number, ttl=90) == 1
    assert await context_redis_state.increment_mandate_warning_count(phone_number, ttl=90) == 2
    assert await context_redis_state.get_mandate_warning_count(phone_number) == 2
    assert redis_stub.expirations[f"user:{phone_number}:mandate_warning_count"] == 90
