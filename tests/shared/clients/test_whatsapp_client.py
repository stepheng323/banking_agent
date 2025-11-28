import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from shared.clients.whatsapp_client import WhatsAppClient
import httpx

@pytest.mark.asyncio
async def test_whatsapp_client_init(mock_env_vars):
    """Test WhatsAppClient initialization."""
    with patch("shared.clients.whatsapp_client.settings") as mock_settings:
        mock_settings.meta_access_token = "test_token"
        mock_settings.meta_phone_number_id = "test_phone_id"
        client = WhatsAppClient()
        assert client.access_token == "test_token"
        assert client.phone_number_id == "test_phone_id"

@pytest.mark.asyncio
async def test_send_text_success(mock_env_vars):
    """Test sending a text message successfully."""
    client = WhatsAppClient()
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"messages": [{"id": "test_id"}]}
    
    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = mock_response
    
    with patch("httpx.AsyncClient", return_value=mock_client_instance) as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client_instance
        
        response = await client.send_text("1234567890", "Hello")
        
        assert response == {"messages": [{"id": "test_id"}]}
        mock_client_instance.post.assert_called_once()
        
        # Verify payload
        call_args = mock_client_instance.post.call_args
        assert call_args[1]["json"]["to"] == "1234567890"
        assert call_args[1]["json"]["text"]["body"] == "Hello"

@pytest.mark.asyncio
async def test_send_text_failure(mock_env_vars):
    """Test sending a text message failure."""
    client = WhatsAppClient()
    
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Error", request=MagicMock(), response=mock_response
    )
    # Important: json() is called in the exception handler to print error body
    mock_response.json.return_value = {"error": "Bad Request"}
    
    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = mock_response
    
    with patch("httpx.AsyncClient", return_value=mock_client_instance) as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client_instance
        
        with pytest.raises(httpx.HTTPStatusError):
            await client.send_text("1234567890", "Hello")

@pytest.mark.asyncio
async def test_send_flow_success(mock_env_vars):
    """Test sending a flow message successfully."""
    client = WhatsAppClient()
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"messages": [{"id": "test_id"}]}
    
    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = mock_response
    
    with patch("httpx.AsyncClient", return_value=mock_client_instance) as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client_instance
        
        response = await client.send_flow(
            to="1234567890",
            flow_id="flow_123",
            flow_cta="Start",
            screen_name="screen_1",
            header="Header",
            text_body="Body"
        )
        
        assert response == {"messages": [{"id": "test_id"}]}
        
        # Verify payload
        call_args = mock_client_instance.post.call_args
        payload = call_args[1]["json"]
        assert payload["interactive"]["type"] == "flow"
        assert payload["interactive"]["action"]["parameters"]["flow_id"] == "flow_123"
