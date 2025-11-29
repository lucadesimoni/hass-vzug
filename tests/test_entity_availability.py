"""Tests for entity availability checks."""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, UTC

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.vzug.sensor import Program
from custom_components.vzug.helpers import UserConfigEntity
from custom_components.vzug.update import VZugUpdate
from custom_components.vzug.button import CheckUpdate
from custom_components.vzug.shared import Shared
import custom_components.vzug.api as api
from yarl import URL


@pytest.fixture
def mock_shared():
    """Create a mock Shared object for testing."""
    hass = MagicMock(spec=HomeAssistant)
    base_url = URL("http://example.com")
    shared = Shared(hass, base_url, None)
    
    # Mock coordinators with successful updates
    shared.state_coord = MagicMock(spec=DataUpdateCoordinator)
    shared.state_coord.last_update_success = True
    shared.state_coord.data = api.AggState(
        zh_mode=-1,
        device={"Program": "Eco", "Status": "Running"},
        device_fetched_at=datetime.now(UTC),
        notifications=[],
        eco_info={},
    )
    
    shared.update_coord = MagicMock(spec=DataUpdateCoordinator)
    shared.update_coord.last_update_success = True
    shared.update_coord.data = api.AggUpdateStatus(
        update={},
        ai_fw_version={"SW": "1.0.0"},
        hh_fw_version={},
    )
    
    shared.config_coord = MagicMock(spec=DataUpdateCoordinator)
    shared.config_coord.last_update_success = True
    shared.config_coord.data = {}
    
    shared.unique_id_prefix = "00:11:22:33:44:55"
    shared.device_info = {}
    shared.meta = api.AggMeta(
        mac_address="00:11:22:33:44:55",
        model_id="TEST",
        model_name="Test Device",
        device_name="Test",
        serial_number="123456",
        api_version=(1, 7, 0),
    )
    
    return shared


def test_state_base_available_when_coordinator_successful(mock_shared):
    """Test that StateBase entities are available when coordinator is successful."""
    entity = Program(mock_shared)
    
    assert entity.available is True


def test_state_base_unavailable_when_coordinator_failed(mock_shared):
    """Test that StateBase entities are unavailable when coordinator failed."""
    mock_shared.state_coord.last_update_success = False
    
    entity = Program(mock_shared)
    
    assert entity.available is False


def test_state_base_unavailable_when_data_none(mock_shared):
    """Test that StateBase entities are unavailable when data is None."""
    mock_shared.state_coord.data = None
    
    entity = Program(mock_shared)
    
    assert entity.available is False


def test_user_config_entity_available_when_successful(mock_shared):
    """Test that UserConfigEntity is available when coordinator is successful."""
    # Create a mock category and command
    category = api.AggCategory(
        key="CATEGORY_0",
        description="Test Category",
        commands={
            "test_command": api.Command(
                type="boolean",
                command="test_command",
                value="true",
            )
        },
    )
    mock_shared.config_coord.data = {"CATEGORY_0": category}
    
    entity = UserConfigEntity(
        mock_shared, category_key="CATEGORY_0", command_key="test_command"
    )
    
    assert entity.available is True


def test_user_config_entity_unavailable_when_failed(mock_shared):
    """Test that UserConfigEntity is unavailable when coordinator failed."""
    mock_shared.config_coord.last_update_success = False
    
    category = api.AggCategory(
        key="CATEGORY_0",
        description="Test Category",
        commands={"test_command": api.Command()},
    )
    mock_shared.config_coord.data = {"CATEGORY_0": category}
    
    entity = UserConfigEntity(
        mock_shared, category_key="CATEGORY_0", command_key="test_command"
    )
    
    assert entity.available is False


def test_update_entity_available_when_successful(mock_shared):
    """Test that VZugUpdate entity is available when coordinator is successful."""
    entity = VZugUpdate(mock_shared)
    
    assert entity.available is True


def test_update_entity_unavailable_when_failed(mock_shared):
    """Test that VZugUpdate entity is unavailable when coordinator failed."""
    mock_shared.update_coord.last_update_success = False
    
    entity = VZugUpdate(mock_shared)
    
    assert entity.available is False


def test_check_update_button_available_when_successful(mock_shared):
    """Test that CheckUpdate button is available when coordinator is successful."""
    entity = CheckUpdate(mock_shared)
    
    assert entity.available is True


def test_check_update_button_unavailable_when_failed(mock_shared):
    """Test that CheckUpdate button is unavailable when coordinator failed."""
    mock_shared.update_coord.last_update_success = False
    
    entity = CheckUpdate(mock_shared)
    
    assert entity.available is False


def test_check_update_button_unavailable_when_data_none(mock_shared):
    """Test that CheckUpdate button is unavailable when data is None."""
    mock_shared.update_coord.data = None
    
    entity = CheckUpdate(mock_shared)
    
    assert entity.available is False

