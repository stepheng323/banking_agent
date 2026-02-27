from unittest.mock import AsyncMock

import pytest

import apps.core.src.main as core_main


@pytest.mark.asyncio
async def test_core_lifespan_runs_in_api_only_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    warm_runtime = AsyncMock()
    monkeypatch.setattr(core_main, "warm_runtime", warm_runtime)

    async with core_main.lifespan(core_main.app):
        pass

    warm_runtime.assert_awaited_once()
