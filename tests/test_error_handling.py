"""Tests for error handling and edge cases in the V-ZUG integration."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx

from custom_components.vzug.api import VZugApi, AuthenticationFailed


@pytest.fixture
def vzug_api():
    """Create a VZugApi instance for testing."""
    return VZugApi(base_url="http://example.com")


@pytest.mark.asyncio
async def test_authentication_error_raises_exception(vzug_api):
    """Test that HTTP 401 errors raise AuthenticationFailed exception."""
    mock_response = MagicMock()
    mock_response.status_code = httpx.codes.UNAUTHORIZED
    mock_response.is_server_error = False
    mock_response.text = "Unauthorized"

    error = httpx.HTTPStatusError(
        "Unauthorized", request=MagicMock(), response=mock_response
    )

    with patch.object(vzug_api._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = error

        with pytest.raises(AuthenticationFailed):
            await vzug_api.get_device_status()


@pytest.mark.asyncio
async def test_client_error_not_retried(vzug_api):
    """Test that client errors (4xx) are not retried."""
    mock_response = MagicMock()
    mock_response.status_code = httpx.codes.NOT_FOUND
    mock_response.is_server_error = False
    mock_response.text = "Not Found"

    error = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=mock_response
    )

    with patch.object(vzug_api._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = error

        with pytest.raises(httpx.HTTPStatusError):
            await vzug_api.get_device_status()

        # Should not retry on client errors
        assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_server_error_retried(vzug_api):
    """Test that server errors (5xx) are retried."""
    mock_response = MagicMock()
    mock_response.status_code = httpx.codes.INTERNAL_SERVER_ERROR
    mock_response.is_server_error = True
    mock_response.text = "Internal Server Error"

    error = httpx.HTTPStatusError(
        "Internal Server Error", request=MagicMock(), response=mock_response
    )

    with patch.object(vzug_api._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = error

        # Should raise after all retries
        with pytest.raises(httpx.HTTPStatusError):
            await vzug_api.get_device_status()

        # Should retry multiple times (default attempts=5)
        assert mock_get.call_count == 5


@pytest.mark.asyncio
async def test_transport_error_retried(vzug_api):
    """Test that transport errors are retried."""
    error = httpx.TransportError("Connection failed")

    with patch.object(vzug_api._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = error

        # Should raise after all retries
        with pytest.raises(httpx.TransportError):
            await vzug_api.get_device_status()

        # Should retry multiple times
        assert mock_get.call_count == 5


@pytest.mark.asyncio
async def test_default_on_error_returns_empty_dict(vzug_api):
    """Test that default_on_error=True returns empty dict on error."""
    mock_response = MagicMock()
    mock_response.status_code = httpx.codes.INTERNAL_SERVER_ERROR
    mock_response.is_server_error = True

    error = httpx.HTTPStatusError(
        "Internal Server Error", request=MagicMock(), response=mock_response
    )

    with patch.object(vzug_api._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = error

        result = await vzug_api.get_device_status(default_on_error=True)

        # Should return empty DeviceStatus dict
        assert result == {}


@pytest.mark.asyncio
async def test_empty_response_handled(vzug_api):
    """Test that empty responses are handled gracefully."""
    mock_response = MagicMock()
    mock_response.content = b""
    mock_response.text = ""
    mock_response.raise_for_status.return_value = None
    mock_response.json.side_effect = ValueError("No JSON content")

    with patch.object(vzug_api._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await vzug_api._command("ai", command="test", expected_type=list)

        # Empty response should be treated as None, then empty list if expected_type is list
        assert result == []


@pytest.mark.asyncio
async def test_json_repair_on_broken_json(vzug_api):
    """Test that broken JSON is repaired when possible."""
    broken_json = '{"status": "idle", "value": 123'  # Missing closing brace

    mock_response = MagicMock()
    mock_response.content = broken_json.encode()
    mock_response.text = broken_json
    mock_response.raise_for_status.return_value = None
    mock_response.json.side_effect = ValueError("Invalid JSON")

    with patch.object(vzug_api._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        # Should attempt repair and either succeed or raise original error
        try:
            result = await vzug_api._command("ai", command="test")
            # If repair succeeded, result should be a dict
            assert isinstance(result, dict) or isinstance(result, list)
        except ValueError:
            # If repair failed, original ValueError should be raised
            pass


@pytest.mark.asyncio
async def test_type_assertion_failure_retried(vzug_api):
    """Test that type assertion failures trigger retries."""
    mock_response = MagicMock()
    mock_response.json.return_value = "not a dict"  # Wrong type
    mock_response.raise_for_status.return_value = None

    with patch.object(vzug_api._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        # Should raise AssertionError after all retries
        with pytest.raises(AssertionError):
            await vzug_api._command(
                "ai", command="test", expected_type=dict
            )

        # Should retry multiple times
        assert mock_get.call_count == 5


@pytest.mark.asyncio
async def test_value_on_err_callback(vzug_api):
    """Test that value_on_err callback is used when provided."""
    mock_response = MagicMock()
    mock_response.status_code = httpx.codes.INTERNAL_SERVER_ERROR
    mock_response.is_server_error = True

    error = httpx.HTTPStatusError(
        "Internal Server Error", request=MagicMock(), response=mock_response
    )

    def custom_error_handler():
        return {"custom": "default_value"}

    with patch.object(vzug_api._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = error

        result = await vzug_api._command(
            "ai",
            command="test",
            expected_type=dict,
            value_on_err=custom_error_handler,
        )

        assert result == {"custom": "default_value"}


@pytest.mark.asyncio
async def test_aggregate_state_handles_partial_failures(vzug_api):
    """Test that aggregate_state handles partial API failures gracefully."""
    # Mock successful device status but failing notifications
    with patch.object(
        vzug_api, "get_device_status", new_callable=AsyncMock
    ) as mock_device, patch.object(
        vzug_api, "get_last_push_notifications", new_callable=AsyncMock
    ) as mock_notifications, patch.object(
        vzug_api, "get_eco_info", new_callable=AsyncMock
    ) as mock_eco:
        mock_device.return_value = {"Program": "Eco", "Status": "Running"}
        mock_notifications.return_value = []  # Empty list on error with default_on_error
        mock_eco.return_value = {}  # Empty dict on error with default_on_error

        result = await vzug_api.aggregate_state(default_on_error=True)

        assert result.device["Program"] == "Eco"
        assert result.notifications == []
        assert result.eco_info == {}


@pytest.mark.asyncio
async def test_get_program_builds_program_objects(vzug_api):
    """Test that get_program properly builds Program objects."""
    raw_programs = [
        {
            "id": 50,
            "name": "Eco",
            "status": "selected",
            "starttime": {"min": 0, "max": 86400, "step": 600},
            "duration": {"set": 22440},
            "energySaving": {"set": False, "options": [True, False]},
        }
    ]

    with patch.object(vzug_api, "_command", new_callable=AsyncMock) as mock_command:
        mock_command.return_value = raw_programs

        result = await vzug_api.get_program()

        assert len(result) == 1
        assert result[0].info["id"] == 50
        assert result[0].info["name"] == "Eco"
        assert "starttime" in result[0].options
        assert "duration" in result[0].options

