"""Unit tests for BankCacheService."""

import pytest
import json
from unittest.mock import AsyncMock

from shared.cache.bank_cache import BankCacheService


class TestBankCacheServiceGetBankCode:
    """Tests for BankCacheService.get_bank_code method."""

    @pytest.fixture
    def bank_cache(self, mock_redis, sample_banks):
        """Create a BankCacheService with mocked Redis containing sample banks."""
        mock_redis.get = AsyncMock(return_value=json.dumps(sample_banks))
        return BankCacheService(redis_client=mock_redis)

    @pytest.mark.asyncio
    async def test_get_bank_code_gtb(self, bank_cache):
        """GTB abbreviation should return GTBank code."""
        code = await bank_cache.get_bank_code("GTB")
        assert code == "058"

    @pytest.mark.asyncio
    async def test_get_bank_code_uba(self, bank_cache):
        """UBA should return UBA bank code."""
        code = await bank_cache.get_bank_code("UBA")
        assert code == "033"

    @pytest.mark.asyncio
    async def test_get_bank_code_access(self, bank_cache):
        """Access should return Access Bank code."""
        code = await bank_cache.get_bank_code("access")
        assert code == "044"

    @pytest.mark.asyncio
    async def test_get_bank_code_full_name(self, bank_cache):
        """Full bank name should resolve correctly."""
        code = await bank_cache.get_bank_code("United Bank For Africa")
        assert code == "033"

    @pytest.mark.asyncio
    async def test_get_bank_code_opay(self, bank_cache):
        """OPay fintech should resolve correctly."""
        code = await bank_cache.get_bank_code("OPay")
        assert code == "999992"

    @pytest.mark.asyncio
    async def test_get_bank_code_kuda(self, bank_cache):
        """Kuda should resolve correctly."""
        code = await bank_cache.get_bank_code("Kuda")
        assert code == "999240"

    @pytest.mark.asyncio
    async def test_get_bank_code_not_found(self, bank_cache):
        """Unknown bank should return None."""
        code = await bank_cache.get_bank_code("NonexistentBank")
        assert code is None

    @pytest.mark.asyncio
    async def test_get_bank_code_empty_cache(self, mock_redis):
        """Empty cache should return None."""
        mock_redis.get = AsyncMock(return_value=None)
        bank_cache = BankCacheService(redis_client=mock_redis)
        
        code = await bank_cache.get_bank_code("GTB")
        assert code is None

    @pytest.mark.asyncio
    async def test_get_bank_code_first_bank(self, bank_cache):
        """First Bank variations should resolve correctly."""
        code = await bank_cache.get_bank_code("first bank")
        assert code == "011"
