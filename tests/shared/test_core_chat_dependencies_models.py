from __future__ import annotations

import pytest

from apps.chat.src.runtime import model_roles


def test_resolve_role_model_selects_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    infos: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        model_roles.logger,
        "info",
        lambda event, **kwargs: infos.append((event, kwargs)),
    )

    model = model_roles.resolve_role_model(
        role="query",
        configured_model="gpt-5.4-nano",
        planner_model="gpt-4o-mini",
        app_env="production",
    )

    assert model == "gpt-5.4-nano"
    assert infos == [
        (
            "query_model_selected",
            {
                "app_env": "production",
                "model": "gpt-5.4-nano",
            },
        )
    ]


def test_resolve_role_model_falls_back_to_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    infos: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        model_roles.logger,
        "info",
        lambda event, **kwargs: infos.append((event, kwargs)),
    )

    model = model_roles.resolve_role_model(
        role="query",
        configured_model="",
        planner_model="gpt-4o-mini",
        app_env="production",
    )

    assert model == "gpt-4o-mini"
    assert infos == [
        (
            "query_model_selected",
            {
                "app_env": "production",
                "model": "gpt-4o-mini",
            },
        )
    ]


def test_chat_role_models_use_dedicated_conversation_client_and_zero_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[dict[str, object]] = []

    class _FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            clients.append(kwargs)

    monkeypatch.setattr(model_roles, "ChatOpenAI", _FakeChatOpenAI)
    monkeypatch.setattr(model_roles, "build_llm_http_async_client", lambda: object())

    roles = model_roles.build_chat_role_models(
        planner_model="planner-model",
        query_model="query-model",
        semantic_router_model="semantic-model",
        conversation_model="gpt-5.4-nano",
        interrupt_router_model="interrupt-model",
        extractor_model="extractor-model",
        app_env="test",
    )

    assert roles.conversation_model == "gpt-5.4-nano"
    assert clients[3]["model"] == "gpt-5.4-nano"
    assert clients[3]["timeout"] == 10.0
    assert all(client["max_retries"] == 0 for client in clients)
    assert [client["timeout"] for client in clients] == [20.0, 15.0, 12.0, 10.0, 12.0, 15.0]
