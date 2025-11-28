import pytest
import os
from unittest.mock import MagicMock

@pytest.fixture
def mock_env_vars(monkeypatch):
    """Mock environment variables."""
    monkeypatch.setenv("META_ACCESS_TOKEN", "test_token")
    monkeypatch.setenv("META_PHONE_NUMBER_ID", "test_phone_id")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")

@pytest.fixture
def mock_whatsapp_client():
    """Mock WhatsApp client."""
    client = MagicMock()
    client.send_text.return_value = {"messages": [{"id": "test_id"}]}
    client.send_flow.return_value = {"messages": [{"id": "test_id"}]}
    return client
