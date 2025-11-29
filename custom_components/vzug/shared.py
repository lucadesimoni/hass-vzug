import contextlib
import logging
from datetime import timedelta

import yarl
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import api
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

StateCoordinator = DataUpdateCoordinator[api.AggState]
UpdateCoordinator = DataUpdateCoordinator[api.AggUpdateStatus]
UPDATE_COORD_IDLE_INTERVAL = timedelta(hours=6)
UPDATE_COORD_ACTIVE_INTERVAL = timedelta(seconds=5)

ConfigCoordinator = DataUpdateCoordinator[api.AggConfig]


class Shared:
    """Shared coordinator and state management for a V-ZUG device.

    This class manages all coordinators (state, update, config) and device
    metadata for a single V-ZUG device integration instance. It's shared
    across all entities for the device.

    Attributes:
        hass: Home Assistant instance.
        client: VZugApi client for communicating with the device.
        state_coord: Coordinator for device state updates (30s interval).
        update_coord: Coordinator for firmware update status (adaptive interval).
        config_coord: Coordinator for device configuration (5min interval).
        unique_id_prefix: MAC address used as prefix for entity unique IDs.
        meta: Aggregated device metadata.
        device_info: Home Assistant DeviceInfo for device registry.
    """

    hass: HomeAssistant
    client: api.VZugApi

    state_coord: StateCoordinator
    update_coord: UpdateCoordinator
    config_coord: ConfigCoordinator

    unique_id_prefix: str
    meta: api.AggMeta
    device_info: DeviceInfo

    def __init__(
        self,
        hass: HomeAssistant,
        base_url: yarl.URL,
        credentials: api.Credentials | None,
    ) -> None:
        """Initialize Shared instance for a V-ZUG device.

        Args:
            hass: Home Assistant instance.
            base_url: Base URL of the V-ZUG device.
            credentials: Optional credentials for authentication.
        """
        self.hass = hass
        self.client = api.VZugApi(
            base_url,
            credentials=credentials,
        )

        self.state_coord = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name="state",
            update_interval=timedelta(seconds=30),
            update_method=self._fetch_state,
        )
        self.update_coord = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name="update",
            update_interval=UPDATE_COORD_IDLE_INTERVAL,
            update_method=self._fetch_update,
        )
        self.config_coord = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name="config",
            update_interval=timedelta(minutes=5),
            update_method=self._fetch_config,
        )

        # the rest will be set on first refresh
        self.unique_id_prefix = ""
        self.device_info = DeviceInfo()
        self._first_refresh_done = False

    async def async_config_entry_first_refresh(self) -> None:
        """Perform initial refresh of all coordinators and setup device info.

        This method should be called once when a config entry is first loaded.
        It fetches device metadata and performs initial coordinator refreshes.

        Raises:
            ConfigEntryNotReady: If initialization fails.
            ConfigEntryAuthFailed: If authentication fails.
        """
        async with detect_auth_failed():
            self.meta = await self.client.aggregate_meta()

        await self.state_coord.async_config_entry_first_refresh()
        await self.update_coord.async_config_entry_first_refresh()
        await self.config_coord.async_config_entry_first_refresh()

        try:
            await self._post_first_refresh()
        except Exception as exc:
            _LOGGER.exception("init failed")
            raise ConfigEntryNotReady() from exc

    async def async_shutdown(self) -> None:
        await self.state_coord.async_shutdown()
        await self.update_coord.async_shutdown()
        await self.config_coord.async_shutdown()

    async def _post_first_refresh(self) -> None:
        mac_addr = dr.format_mac(self.meta.mac_address)
        self.unique_id_prefix = mac_addr
        if not self.unique_id_prefix:
            _LOGGER.warning(
                "unable to determine unique id from device data: %s", self.meta
            )

        self.device_info.update(
            DeviceInfo(
                configuration_url=str(self.client.base_url),
                identifiers={(DOMAIN, self.meta.serial_number)},
                name=self.meta.create_name(),
                hw_version=self.update_coord.data.ai_fw_version.get("HW"),
                sw_version=self.update_coord.data.ai_fw_version.get("SW"),
                connections={(dr.CONNECTION_NETWORK_MAC, mac_addr)},
                model=self.meta.model_name,
            )
        )

        self._first_refresh_done = True

    async def _fetch_state(self) -> api.AggState:
        async with detect_auth_failed():
            return await self.client.aggregate_state(
                default_on_error=self._first_refresh_done
            )

    async def _fetch_update(self) -> api.AggUpdateStatus:
        async with detect_auth_failed():
            data = await self.client.aggregate_update_status(
                supports_update_status=self.meta.supports_update_status(),
                default_on_error=True,  # we allow the update to fail because it's not essential
            )
        if data.update.get("status") in ("idle", None):
            self.update_coord.update_interval = UPDATE_COORD_IDLE_INTERVAL
        else:
            self.update_coord.update_interval = UPDATE_COORD_ACTIVE_INTERVAL
        return data

    async def _fetch_config(self) -> api.AggConfig:
        async with detect_auth_failed():
            return await self.client.aggregate_config()


@contextlib.asynccontextmanager
async def detect_auth_failed():
    """Context manager to detect authentication failures.

    Converts api.AuthenticationFailed exceptions to ConfigEntryAuthFailed
    so Home Assistant can properly handle re-authentication flows.

    Yields:
        None

    Raises:
        ConfigEntryAuthFailed: If authentication fails during the context.
    """
    try:
        yield
    except api.AuthenticationFailed:
        raise ConfigEntryAuthFailed
