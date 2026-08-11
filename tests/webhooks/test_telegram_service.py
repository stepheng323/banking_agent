from types import SimpleNamespace
from typing import Any

import pytest

from apps.gateway.adapters.telegram import parse_update
from apps.gateway.api.webhooks.telegram import service as telegram_service_module
from apps.gateway.api.webhooks.telegram.service import TelegramWebhookService
from shared.cache.flow_session_manager import SessionReadResult


class _PublisherStub:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        self.published.append((topic, message))


class _UserRepositoryStub:
    def __init__(self) -> None:
        self.channel_identity_calls = 0

    async def get_by_channel_identity(self, channel: str, identity: str) -> Any:
        assert channel == "telegram"
        assert identity == "12345"
        self.channel_identity_calls += 1
        return SimpleNamespace(id="user-1")


def test_telegram_first_name_reaches_channel_metadata() -> None:
    parsed = parse_update(
        {
            "message": {
                "message_id": 42,
                "chat": {"id": 12345},
                "from": {"id": 12345, "first_name": "Gaines"},
                "text": "hi",
            }
        }
    )
    assert parsed is not None

    service = TelegramWebhookService.__new__(TelegramWebhookService)
    channel_message = service._build_message(parsed)

    assert channel_message.channel_metadata["sender_display_name"] == "Gaines"


class _RedisStub:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        del ex
        self.values[key] = value


class _SessionManagerStub:
    def __init__(self) -> None:
        self.redis = _RedisStub()
        self.ttl = 900
        self.sessions: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []

    async def update_session_strict(
        self,
        flow_token: str,
        updates: dict[str, Any],
        *,
        verify: bool = False,
    ) -> bool:
        del verify
        self.sessions.setdefault(flow_token, {}).update(updates)
        return True

    async def read_session(self, flow_token: str) -> SessionReadResult:
        session = self.sessions.get(flow_token)
        if session is None:
            return SessionReadResult(status="missing")
        return SessionReadResult(status="found", data=session)

    async def delete_session(self, flow_token: str) -> None:
        self.deleted.append(flow_token)
        self.sessions.pop(flow_token, None)


class _TelegramClientStub:
    def __init__(self) -> None:
        self.typing_calls: list[str] = []
        self.text_calls: list[dict[str, str]] = []
        self.api_calls: list[tuple[str, dict[str, Any]]] = []
        self.callback_answers: list[str] = []

    async def _call(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        del files, max_retries
        self.api_calls.append((method, payload or {}))
        return {"ok": True, "result": {"message_id": 77}}

    async def send_typing_indicator(self, chat_id: str) -> bool:
        self.typing_calls.append(chat_id)
        return True

    async def send_text(self, *, to: str, text: str) -> dict[str, Any]:
        self.text_calls.append({"to": to, "text": text})
        return {"ok": True}

    async def answer_callback_query(self, callback_query_id: str, text: str = "") -> bool:
        del text
        self.callback_answers.append(callback_query_id)
        return True


class _WhatsAppClientStub:
    def __init__(self) -> None:
        self.button_calls: list[dict[str, Any]] = []
        self.flow_calls: list[dict[str, Any]] = []
        self.text_calls: list[dict[str, Any]] = []

    async def send_button(self, **kwargs: Any) -> dict[str, Any]:
        self.button_calls.append(kwargs)
        return {"ok": True}

    async def send_flow(self, **kwargs: Any) -> Any:
        self.flow_calls.append(kwargs)
        return SimpleNamespace(success=True, error=None, message_id="wamid-flow")

    async def send_text(self, **kwargs: Any) -> dict[str, Any]:
        self.text_calls.append(kwargs)
        return {"ok": True}


def test_parse_update_preserves_telegram_photo_caption_and_file_id() -> None:
    parsed = parse_update(
        {
            "update_id": 10,
            "message": {
                "message_id": 200,
                "chat": {"id": 12345},
                "from": {"id": 12345, "first_name": "Gaines"},
                "caption": "send 5k for groceries",
                "photo": [
                    {"file_id": "small-photo", "width": 90, "height": 90},
                    {"file_id": "large-photo", "width": 1280, "height": 1280},
                ],
            },
        }
    )

    assert parsed is not None
    assert parsed.type == "photo"
    assert parsed.text == "send 5k for groceries"
    assert parsed.photo_file_id == "large-photo"


def test_parse_update_preserves_telegram_audio_caption() -> None:
    parsed = parse_update(
        {
            "update_id": 11,
            "message": {
                "message_id": 201,
                "chat": {"id": 12345},
                "from": {"id": 12345, "first_name": "Gaines"},
                "caption": "send it today",
                "audio": {"file_id": "audio-file", "mime_type": "audio/mpeg"},
            },
        }
    )

    assert parsed is not None
    assert parsed.type == "audio"
    assert parsed.text == "send it today"
    assert parsed.audio_file_id == "audio-file"
    assert parsed.mime_type == "audio/mpeg"


def test_parse_update_maps_image_document_to_image_media() -> None:
    parsed = parse_update(
        {
            "update_id": 12,
            "message": {
                "message_id": 202,
                "chat": {"id": 12345},
                "from": {"id": 12345, "first_name": "Gaines"},
                "caption": "use this account",
                "document": {"file_id": "doc-image", "mime_type": "image/png"},
            },
        }
    )

    assert parsed is not None
    assert parsed.type == "document_image"
    assert parsed.text == "use this account"
    assert parsed.document_file_id == "doc-image"
    assert parsed.mime_type == "image/png"


@pytest.mark.asyncio
async def test_process_update_enqueues_linked_image_document() -> None:
    publisher = _PublisherStub()
    telegram_client = _TelegramClientStub()
    user_repository = _UserRepositoryStub()
    service = TelegramWebhookService(
        publisher=publisher,
        user_repository=user_repository,
        telegram_client=telegram_client,  # type: ignore[arg-type]
    )

    handled = await service.process_update(
        {
            "update_id": 13,
            "message": {
                "message_id": 203,
                "chat": {"id": 12345},
                "from": {"id": 12345, "first_name": "Gaines"},
                "caption": "send 5k for groceries",
                "document": {"file_id": "doc-image", "mime_type": "image/png"},
            },
        }
    )

    assert handled is True
    assert len(publisher.published) == 1
    topic, payload = publisher.published[0]
    assert topic == "message.received"
    assert payload["message_type"] == "image"
    assert payload["text"] == "send 5k for groceries"
    assert payload["media_id"] == "doc-image"
    assert payload["mime_type"] == "image/png"
    assert payload["channel"] == "telegram"


@pytest.mark.asyncio
async def test_process_update_enqueues_linked_text_without_eager_typing() -> None:
    publisher = _PublisherStub()
    telegram_client = _TelegramClientStub()
    user_repository = _UserRepositoryStub()
    service = TelegramWebhookService(
        publisher=publisher,
        user_repository=user_repository,
        telegram_client=telegram_client,  # type: ignore[arg-type]
    )

    handled = await service.process_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 99,
                "chat": {"id": 12345},
                "from": {"id": 12345, "first_name": "Gaines"},
                "text": "How much did I send to mum this week",
            },
        }
    )

    assert handled is True
    assert telegram_client.typing_calls == []
    assert telegram_client.text_calls == []
    assert len(publisher.published) == 1
    topic, payload = publisher.published[0]
    assert topic == "message.received"
    assert payload["message_id"] == "99"
    assert payload["channel_user_id"] == "12345"
    assert payload["message_type"] == "text"
    assert payload["text"] == "How much did I send to mum this week"
    assert payload["channel"] == "telegram"
    assert user_repository.channel_identity_calls == 1


@pytest.mark.asyncio
async def test_process_update_uses_cached_linked_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_stub = _RedisStub(
        {
            "cache:channel_identity:telegram:12345": (
                '{"id": "dbfea933-7738-4f87-8a15-98ba39da189c", "phone_number": "2348162511023", '
                '"onboarding_status": "onboarding_completed", "full_name": "Gaines"}'
            )
        }
    )
    monkeypatch.setattr("shared.cache.channel_identity_cache.RedisClient.get_client", lambda: redis_stub)
    publisher = _PublisherStub()
    telegram_client = _TelegramClientStub()
    user_repository = _UserRepositoryStub()
    service = TelegramWebhookService(
        publisher=publisher,
        user_repository=user_repository,
        telegram_client=telegram_client,  # type: ignore[arg-type]
        redis_client=redis_stub,
    )

    handled = await service.process_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 99,
                "chat": {"id": 12345},
                "from": {"id": 12345, "first_name": "Gaines"},
                "text": "Hello",
            },
        }
    )

    assert handled is True
    assert len(publisher.published) == 1
    assert user_repository.channel_identity_calls == 0


@pytest.mark.asyncio
async def test_telegram_web_app_pin_data_is_not_a_supported_payload() -> None:
    publisher = _PublisherStub()
    telegram_client = _TelegramClientStub()
    service = TelegramWebhookService(
        publisher=publisher,
        user_repository=_UserRepositoryStub(),  # type: ignore[arg-type]
        telegram_client=telegram_client,  # type: ignore[arg-type]
    )

    handled = await service.process_update(
        {
            "update_id": 6,
            "message": {
                "message_id": 104,
                "chat": {"id": 12345},
                "from": {"id": 12345, "first_name": "Gaines"},
                "web_app_data": {
                    "data": '{"flow_token": "transfer-pin-idem-1-12345", "pin": "123456"}',
                },
            },
        }
    )

    assert handled is False
    assert publisher.published == []
    assert telegram_client.text_calls == []


@pytest.mark.asyncio
async def test_contact_share_for_new_user_creates_opaque_onboarding_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NewUserRepositoryStub:
        async def get_by_channel_identity(self, channel: str, identity: str) -> Any:
            assert channel == "telegram"
            assert identity == "12345"
            return None

        async def get_by_phone(self, phone_number: str) -> Any:
            assert phone_number == "2348162511023"
            return None

        async def link_channel_identity(self, user_id: str, channel: str, identity: str) -> None:
            del user_id, channel, identity
            raise AssertionError("new Telegram users should not be linked until onboarding completes")

    async def _load_channel_identity_user(channel: str, identity: str) -> Any:
        assert channel == "telegram"
        assert identity == "12345"
        return None

    async def _store_channel_identity_user(channel: str, identity: str, user: Any) -> None:
        del channel, identity, user
        raise AssertionError("no user should be cached before onboarding completes")

    session_manager = _SessionManagerStub()
    monkeypatch.setattr(telegram_service_module, "session_manager", session_manager)
    monkeypatch.setattr(telegram_service_module, "load_channel_identity_user", _load_channel_identity_user)
    monkeypatch.setattr(telegram_service_module, "store_channel_identity_user", _store_channel_identity_user)
    monkeypatch.setattr(telegram_service_module.secrets, "token_urlsafe", lambda _: "tg-opaque-token")
    monkeypatch.setattr(telegram_service_module.settings, "telegram_mini_app_base_url", "https://mini.test")
    bootstrap_calls: list[dict[str, Any]] = []

    async def _create_bootstrap(**kwargs: Any) -> str:
        bootstrap_calls.append(kwargs)
        return "boot-new-user"

    monkeypatch.setattr(telegram_service_module, "create_telegram_miniapp_bootstrap", _create_bootstrap)

    telegram_client = _TelegramClientStub()
    service = telegram_service_module.TelegramWebhookService(
        publisher=_PublisherStub(),  # type: ignore[arg-type]
        user_repository=_NewUserRepositoryStub(),  # type: ignore[arg-type]
        telegram_client=telegram_client,  # type: ignore[arg-type]
    )

    handled = await service.process_update(
        {
            "update_id": 2,
            "message": {
                "message_id": 100,
                "chat": {"id": 12345},
                "from": {"id": 12345, "first_name": "Gaines"},
                "contact": {"phone_number": "+2348162511023", "user_id": 12345},
            },
        }
    )

    assert handled is True
    assert session_manager.sessions["onboarding-tg-opaque-token"] == {
        "phone_number": "2348162511023",
        "channel": "telegram",
        "channel_user_id": "12345",
        "step": "bvn_entry",
        "cta_message_id": "77",
        "cta_chat_id": "12345",
    }
    assert session_manager.redis.values["telegram:onboarding:12345:flow_token"] == "onboarding-tg-opaque-token"

    assert len(telegram_client.api_calls) == 2
    ack_method, ack_payload = telegram_client.api_calls[0]
    assert ack_method == "sendMessage"
    assert ack_payload["text"] == "Thanks. I'll use this number only to start your account setup."
    assert ack_payload["reply_markup"] == {"remove_keyboard": True}

    method, payload = telegram_client.api_calls[1]
    assert method == "sendMessage"
    assert payload["text"] == (
        f"Welcome to {telegram_service_module.settings.app_name}.\n\n"
        "You're almost in. I couldn't find an existing account for this phone number, so let's complete setup."
    )
    assert payload["reply_markup"]["inline_keyboard"][0][0]["text"] == "Complete setup"
    app_url = payload["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"]
    assert "boot=boot-new-user" in app_url
    assert "flow_token=onboarding-tg-opaque-token" not in app_url
    assert "12345" not in app_url
    assert "onboarding-12345" not in app_url
    assert bootstrap_calls == [
        {
            "chat_id": "12345",
            "flow_token": "onboarding-tg-opaque-token",
            "endpoint": "onboarding",
        }
    ]


@pytest.mark.asyncio
async def test_existing_user_contact_share_requires_whatsapp_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ExistingUserRepositoryStub:
        linked_calls: list[tuple[str, str, str]]

        def __init__(self) -> None:
            self.linked_calls = []

        async def get_by_channel_identity(self, channel: str, identity: str) -> Any:
            assert channel == "telegram"
            assert identity == "12345"
            return None

        async def get_by_phone(self, phone_number: str) -> Any:
            assert phone_number == "2348162511023"
            return SimpleNamespace(id="user-1", phone_number=phone_number)

        async def get_channel_identity_by_phone(self, phone_number: str, channel: str) -> str | None:
            assert phone_number == "2348162511023"
            assert channel == "whatsapp"
            return "2348162511023"

        async def link_channel_identity(self, user_id: str, channel: str, identity: str) -> None:
            self.linked_calls.append((user_id, channel, identity))

    async def _load_channel_identity_user(channel: str, identity: str) -> Any:
        assert channel == "telegram"
        assert identity == "12345"
        return None

    session_manager = _SessionManagerStub()
    whatsapp_client = _WhatsAppClientStub()
    monkeypatch.setattr(telegram_service_module, "session_manager", session_manager)
    monkeypatch.setattr(telegram_service_module, "load_channel_identity_user", _load_channel_identity_user)
    monkeypatch.setattr(telegram_service_module.secrets, "token_urlsafe", lambda _: "opaque-token")
    monkeypatch.setattr(telegram_service_module, "WhatsAppClient", lambda: whatsapp_client)

    user_repository = _ExistingUserRepositoryStub()
    telegram_client = _TelegramClientStub()
    service = telegram_service_module.TelegramWebhookService(
        publisher=_PublisherStub(),  # type: ignore[arg-type]
        user_repository=user_repository,  # type: ignore[arg-type]
        telegram_client=telegram_client,  # type: ignore[arg-type]
    )

    handled = await service.process_update(
        {
            "update_id": 3,
            "message": {
                "message_id": 101,
                "chat": {"id": 12345},
                "from": {"id": 12345, "first_name": "Gaines"},
                "contact": {"phone_number": "+2348162511023", "user_id": 12345},
            },
        }
    )

    assert handled is True
    assert user_repository.linked_calls == []
    assert session_manager.sessions["channel-link-opaque-token"] == {
        "purpose": "channel_identity_link",
        "user_id": "user-1",
        "phone_number": "2348162511023",
        "requested_channel": "telegram",
        "requested_channel_user_id": "12345",
        "requested_channel_actor_id": "12345",
        "authorizing_channel": "whatsapp",
        "authorizing_channel_user_id": "2348162511023",
        "step": "pending_existing_channel_authorization",
    }
    assert whatsapp_client.button_calls == []
    assert whatsapp_client.flow_calls == [
        {
            "to": "2348162511023",
            "flow_id": telegram_service_module.settings.whatsapp.pin_confirmation_flow_id,
            "flow_config": {
                "header": "Authorize Telegram link",
                "text_body": (
                    "Enter your transaction PIN to link Telegram to your banking profile. "
                    "Continue only if this request was from you."
                ),
                "flow_cta": "Enter PIN",
                "screen_name": "Pin",
                "flow_token": "channel-link-pin-channel-link-opaque-token",
                "flow_action_payload": {"screen": "Pin"},
            },
            "suppress_typing_indicator": True,
        }
    ]
    assert "secure PIN request" in telegram_client.api_calls[-1][1]["text"]


@pytest.mark.asyncio
async def test_unlinked_user_with_pending_onboarding_gets_continue_setup_cta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NewUserRepositoryStub:
        async def get_by_channel_identity(self, channel: str, identity: str) -> Any:
            assert channel == "telegram"
            assert identity == "12345"
            return None

    async def _load_channel_identity_user(channel: str, identity: str) -> Any:
        assert channel == "telegram"
        assert identity == "12345"
        return None

    session_manager = _SessionManagerStub()
    session_manager.redis.values["telegram:onboarding:12345:flow_token"] = "onboarding-token"
    session_manager.sessions["onboarding-token"] = {
        "phone_number": "2348162511023",
        "step": "bvn_entry",
    }
    monkeypatch.setattr(telegram_service_module, "session_manager", session_manager)
    monkeypatch.setattr(telegram_service_module, "load_channel_identity_user", _load_channel_identity_user)
    monkeypatch.setattr(telegram_service_module.settings, "telegram_mini_app_base_url", "https://mini.test")

    async def _create_bootstrap(**kwargs: Any) -> str:
        assert kwargs == {
            "chat_id": "12345",
            "flow_token": "onboarding-token",
            "endpoint": "onboarding",
        }
        return "boot-resume"

    monkeypatch.setattr(telegram_service_module, "create_telegram_miniapp_bootstrap", _create_bootstrap)

    telegram_client = _TelegramClientStub()
    service = telegram_service_module.TelegramWebhookService(
        publisher=_PublisherStub(),  # type: ignore[arg-type]
        user_repository=_NewUserRepositoryStub(),  # type: ignore[arg-type]
        telegram_client=telegram_client,  # type: ignore[arg-type]
    )

    handled = await service.process_update(
        {
            "update_id": 14,
            "message": {
                "message_id": 204,
                "chat": {"id": 12345},
                "from": {"id": 12345, "first_name": "Gaines"},
                "text": "hi",
            },
        }
    )

    assert handled is True
    assert len(telegram_client.api_calls) == 1
    method, payload = telegram_client.api_calls[0]
    assert method == "sendMessage"
    assert payload["text"] == "You're almost there. Tap below to continue your account setup."
    assert payload["reply_markup"]["inline_keyboard"][0][0]["text"] == "Continue setup"
    assert "boot=boot-resume" in payload["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"]
    assert session_manager.sessions["onboarding-token"]["cta_message_id"] == "77"
    assert session_manager.sessions["onboarding-token"]["cta_chat_id"] == "12345"


@pytest.mark.asyncio
async def test_stale_telegram_button_payload_does_not_link_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    session_manager = _SessionManagerStub()
    session_manager.sessions["channel-link-opaque-token"] = {
        "purpose": "channel_identity_link",
        "user_id": "user-1",
        "phone_number": "2348162511023",
        "requested_channel": "whatsapp",
        "requested_channel_user_id": "2348162511023",
        "requested_channel_actor_id": "2348162511023",
        "authorizing_channel": "telegram",
        "authorizing_channel_user_id": "12345",
        "step": "pending_existing_channel_authorization",
    }
    monkeypatch.setattr(telegram_service_module, "session_manager", session_manager)

    whatsapp_client = _WhatsAppClientStub()
    monkeypatch.setattr(telegram_service_module, "WhatsAppClient", lambda: whatsapp_client)

    telegram_client = _TelegramClientStub()
    service = telegram_service_module.TelegramWebhookService(
        publisher=_PublisherStub(),  # type: ignore[arg-type]
        user_repository=_UserRepositoryStub(),  # type: ignore[arg-type]
        telegram_client=telegram_client,  # type: ignore[arg-type]
    )

    handled = await service.process_update(
        {
            "update_id": 5,
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 12345, "first_name": "Gaines"},
                "message": {
                    "message_id": 103,
                    "chat": {"id": 12345},
                },
                "data": "ch_link_ok:channel-link-opaque-token",
            },
        }
    )

    assert handled is True
    assert telegram_client.callback_answers == ["callback-1"]
    assert session_manager.deleted == []
    assert "requires PIN authorization" in telegram_client.text_calls[-1]["text"]
    assert whatsapp_client.text_calls == []


@pytest.mark.asyncio
async def test_contact_share_rejects_non_self_contact(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RepositoryStub:
        async def get_by_channel_identity(self, channel: str, identity: str) -> Any:
            assert channel == "telegram"
            assert identity == "12345"
            return None

        async def get_by_phone(self, phone_number: str) -> Any:
            del phone_number
            raise AssertionError("non-self contacts must not be trusted as phone proof")

    async def _load_channel_identity_user(channel: str, identity: str) -> Any:
        assert channel == "telegram"
        assert identity == "12345"
        return None

    monkeypatch.setattr(telegram_service_module, "load_channel_identity_user", _load_channel_identity_user)
    telegram_client = _TelegramClientStub()
    service = telegram_service_module.TelegramWebhookService(
        publisher=_PublisherStub(),  # type: ignore[arg-type]
        user_repository=_RepositoryStub(),  # type: ignore[arg-type]
        telegram_client=telegram_client,  # type: ignore[arg-type]
    )

    handled = await service.process_update(
        {
            "update_id": 4,
            "message": {
                "message_id": 102,
                "chat": {"id": 12345},
                "from": {"id": 12345, "first_name": "Gaines"},
                "contact": {"phone_number": "+2348162511023", "user_id": 99999},
            },
        }
    )

    assert handled is True
    assert "share your own Telegram phone number" in telegram_client.api_calls[-1][1]["text"]


@pytest.mark.asyncio
async def test_request_contact_uses_warm_setup_copy() -> None:
    telegram_client = _TelegramClientStub()
    service = telegram_service_module.TelegramWebhookService(
        publisher=_PublisherStub(),  # type: ignore[arg-type]
        user_repository=_UserRepositoryStub(),  # type: ignore[arg-type]
        telegram_client=telegram_client,  # type: ignore[arg-type]
    )

    await service._request_contact("12345")

    method, payload = telegram_client.api_calls[-1]
    assert method == "sendMessage"
    assert payload["text"] == (
        f"Welcome to {telegram_service_module.settings.app_name}.\n\n"
        "To set up your banking access, please share the Telegram phone number you want to use."
    )
    assert payload["reply_markup"] == {
        "keyboard": [[{"text": "Share Contact", "request_contact": True}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


@pytest.mark.asyncio
async def test_pre_onboarding_response_calls_attention_to_share_contact_button() -> None:
    telegram_client = _TelegramClientStub()
    service = telegram_service_module.TelegramWebhookService(
        publisher=_PublisherStub(),  # type: ignore[arg-type]
        user_repository=_UserRepositoryStub(),  # type: ignore[arg-type]
        telegram_client=telegram_client,  # type: ignore[arg-type]
    )

    await service._send_pre_onboarding_response(
        "12345",
        telegram_service_module.PreOnboardingResponse(
            action="respond",
            message_key="pre_onboarding.greeting",
            should_resend_onboarding_cta=True,
        ),
    )

    method, payload = telegram_client.api_calls[-1]
    assert method == "sendMessage"
    assert payload["text"] == (
        f"Welcome to {telegram_service_module.settings.app_name}. "
        "I can help with transfers, airtime/data, balances, and transaction history. "
        "First, let's complete your account setup.\n\n"
        "Tap Share Contact below to continue."
    )
    assert payload["reply_markup"] == {
        "keyboard": [[{"text": "Share Contact", "request_contact": True}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }
