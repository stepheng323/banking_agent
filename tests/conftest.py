"""Pytest configuration and shared fixtures."""

from unittest.mock import AsyncMock

import pytest

# Sample bank data for testing
SAMPLE_BANKS: list[dict[str, str]] = [
    {"id": "1", "code": "058", "name": "GTBank Plc"},
    {"id": "2", "code": "033", "name": "United Bank For Africa"},
    {"id": "3", "code": "044", "name": "Access Bank"},
    {"id": "4", "code": "057", "name": "Zenith Bank"},
    {"id": "5", "code": "011", "name": "First Bank of Nigeria"},
    {"id": "6", "code": "999992", "name": "OPay"},
    {"id": "7", "code": "999991", "name": "PalmPay"},
    {"id": "8", "code": "999240", "name": "Kuda Microfinance Bank"},
]


@pytest.fixture
def sample_banks() -> list[dict[str, str]]:
    """Provide sample bank data for tests."""
    return SAMPLE_BANKS.copy()


@pytest.fixture
def mock_redis():
    """Provide a mock Redis client."""
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.setex = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def mock_bank_cache_with_banks(mock_redis, sample_banks):
    """Provide a BankCacheService with mocked Redis containing sample banks."""
    import json

    mock_redis.get = AsyncMock(return_value=json.dumps(sample_banks))

    from shared.cache.bank_cache import BankCacheService

    return BankCacheService(redis_client=mock_redis)
