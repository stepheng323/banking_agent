from __future__ import annotations

import pytest

from apps.core.src.runtime import core_chat_dependencies


def test_resolve_role_model_warns_when_query_matches_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[tuple[str, dict[str, object]]] = []
    infos: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        core_chat_dependencies.logger,
        "warning",
        lambda event, **kwargs: warnings.append((event, kwargs)),
    )
    monkeypatch.setattr(
        core_chat_dependencies.logger,
        "info",
        lambda event, **kwargs: infos.append((event, kwargs)),
    )

    model = core_chat_dependencies._resolve_role_model(
        role="query",
        configured_model="gpt-4o-mini",
        planner_model="gpt-4o-mini",
        app_env="production",
    )

    assert model == "gpt-4o-mini"
    assert warnings == [
        (
            "query_model_same_as_planner",
            {
                "app_env": "production",
                "model": "gpt-4o-mini",
                "planner_model": "gpt-4o-mini",
                "recommended_env": "QUERY_MODEL",
                "dedicated": False,
                "recommended_model": None,
            },
        )
    ]
    assert infos == []


def test_resolve_role_model_logs_dedicated_interrupt_router_model(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[tuple[str, dict[str, object]]] = []
    infos: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        core_chat_dependencies.logger,
        "warning",
        lambda event, **kwargs: warnings.append((event, kwargs)),
    )
    monkeypatch.setattr(
        core_chat_dependencies.logger,
        "info",
        lambda event, **kwargs: infos.append((event, kwargs)),
    )

    model = core_chat_dependencies._resolve_role_model(
        role="interrupt_router",
        configured_model="gpt-5.4-nano",
        planner_model="gpt-4o-mini",
        app_env="production",
    )

    assert model == "gpt-5.4-nano"
    assert warnings == []
    assert infos == [
        (
            "interrupt_router_model_dedicated",
            {
                "app_env": "production",
                "model": "gpt-5.4-nano",
                "planner_model": "gpt-4o-mini",
                "recommended_env": "INTERRUPT_ROUTER_MODEL",
                "dedicated": True,
                "recommended_model": "gpt-5.4-nano",
            },
        )
    ]


def test_resolve_role_model_logs_dedicated_semantic_router_model(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[tuple[str, dict[str, object]]] = []
    infos: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        core_chat_dependencies.logger,
        "warning",
        lambda event, **kwargs: warnings.append((event, kwargs)),
    )
    monkeypatch.setattr(
        core_chat_dependencies.logger,
        "info",
        lambda event, **kwargs: infos.append((event, kwargs)),
    )

    model = core_chat_dependencies._resolve_role_model(
        role="semantic_router",
        configured_model="gpt-5.4-nano",
        planner_model="gpt-4o-mini",
        app_env="production",
    )

    assert model == "gpt-5.4-nano"
    assert warnings == []
    assert infos == [
        (
            "semantic_router_model_dedicated",
            {
                "app_env": "production",
                "model": "gpt-5.4-nano",
                "planner_model": "gpt-4o-mini",
                "recommended_env": "SEMANTIC_ROUTER_MODEL",
                "dedicated": True,
                "recommended_model": "gpt-5.4-nano",
            },
        )
    ]


def test_resolve_role_model_logs_dedicated_extractor_model(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[tuple[str, dict[str, object]]] = []
    infos: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        core_chat_dependencies.logger,
        "warning",
        lambda event, **kwargs: warnings.append((event, kwargs)),
    )
    monkeypatch.setattr(
        core_chat_dependencies.logger,
        "info",
        lambda event, **kwargs: infos.append((event, kwargs)),
    )

    model = core_chat_dependencies._resolve_role_model(
        role="extractor",
        configured_model="gpt-5.4-mini",
        planner_model="gpt-4o-mini",
        app_env="production",
    )

    assert model == "gpt-5.4-mini"
    assert warnings == []
    assert infos == [
        (
            "extractor_model_dedicated",
            {
                "app_env": "production",
                "model": "gpt-5.4-mini",
                "planner_model": "gpt-4o-mini",
                "recommended_env": "EXTRACTOR_MODEL",
                "dedicated": True,
                "recommended_model": "gpt-5.4-mini",
            },
        )
    ]


def test_resolve_role_model_blank_config_logs_fallback_then_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        core_chat_dependencies.logger,
        "warning",
        lambda event, **kwargs: warnings.append((event, kwargs)),
    )
    monkeypatch.setattr(core_chat_dependencies.logger, "info", lambda event, **kwargs: None)

    model = core_chat_dependencies._resolve_role_model(
        role="interrupt_router",
        configured_model="",
        planner_model="gpt-4o-mini",
        app_env="production",
    )

    assert model == "gpt-4o-mini"
    assert warnings == [
        (
            "interrupt_router_model_missing_fallback",
            {
                "app_env": "production",
                "fallback_model": "gpt-4o-mini",
                "planner_model": "gpt-4o-mini",
                "recommended_env": "INTERRUPT_ROUTER_MODEL",
                "recommended_model": "gpt-5.4-nano",
            },
        ),
        (
            "interrupt_router_model_same_as_planner",
            {
                "app_env": "production",
                "model": "gpt-4o-mini",
                "planner_model": "gpt-4o-mini",
                "recommended_env": "INTERRUPT_ROUTER_MODEL",
                "dedicated": False,
                "recommended_model": "gpt-5.4-nano",
            },
        ),
    ]
