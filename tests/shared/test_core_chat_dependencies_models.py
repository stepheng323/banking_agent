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
