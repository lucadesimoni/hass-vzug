import asyncio
import dataclasses
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict, cast
import json_repair

import httpx
from yarl import URL

from . import discovery  # noqa: F401 # type: ignore

_LOGGER = logging.getLogger(__name__)

DeviceStatusInactiveT = Literal["true"] | Literal["false"]


class DeviceStatusProgramEnd(TypedDict, total=False):
    EndType: str
    End: str


class DeviceStatus(TypedDict, total=False):
    DeviceName: str
    Serial: str
    Inactive: DeviceStatusInactiveT
    Program: str
    Status: str
    ProgramEnd: DeviceStatusProgramEnd
    deviceUuid: str


class UpdateProgress(TypedDict, total=False):
    download: int
    installation: int


class UpdateComponent(TypedDict, total=False):
    name: str
    running: bool
    available: bool
    required: bool
    progress: UpdateProgress


class UpdateStatus(TypedDict, total=False):
    status: Literal["idle"] | str
    isAIUpdateAvailable: bool
    isHHGUpdateAvailable: bool
    isSynced: bool
    components: list[UpdateComponent]


class PushNotification(TypedDict, total=False):
    date: str
    message: str


class Command(TypedDict, total=False):
    type: (
        Literal["action"]
        | Literal["boolean"]
        | Literal["selection"]
        | Literal["status"]
        | Literal["range"]
    )
    description: str
    command: str
    value: str
    alterable: bool
    options: list[str]
    minMax: tuple[str, str]
    refresh: list[str]
    """list of commands to refresh when this command is changed"""


HhFwVersion = TypedDict(
    "HhFwVersion",
    {
        "fn": str,
        "an": str,
        "v": str,
        "vr01": str,
        "v2": str,
        "vr10": str,
        "vi2": str,
        "vh1": str,
        "vh2": str,
        "vr0B": str,
        "vp": str,
        "vr0C": str,
        "vr0E": str,
        "Mh": str,
        "MD": str,
        "Zh": str,
        "ZV": str,
        "ZHSW": str,
        "device-type": str,
    },
    total=False,
)


class AiFwVersion(TypedDict, total=False):
    fn: str
    SW: str
    SD: str
    HW: str
    apiVersion: str
    phy: str
    deviceUuid: str


class EcoInfoMetric(TypedDict, total=False):
    total: float
    average: float
    program: float
    option: float  # sent by adorawash for water, no idea what it is


class EcoInfo(TypedDict, total=False):
    water: EcoInfoMetric
    energy: EcoInfoMetric


class Category(TypedDict, total=False):
    description: str


class DeviceInfo(TypedDict, total=False):
    model: str
    description: str
    """model description"""
    type: Literal["WA"] | str
    name: str
    serialNumber: str
    articleNumber: str
    """the serial number starts with this"""
    apiVersion: str  # seen: 1.5.0 / 1.7.0 / 1.8.0
    zhMode: int


class ProgramOptionA(TypedDict, total=False):
    min: int
    max: int
    step: int


class ProgramOptionB(TypedDict, total=False):
    set: bool
    options: list[Any]


class ProgramOption(ProgramOptionA, ProgramOptionB): ...


class ProgramInfo(TypedDict, total=False):
    id: int
    name: str
    status: Literal["selected"] | str
    stepIds: list[int]


@dataclasses.dataclass(slots=True, kw_only=True)
class Program:
    info: ProgramInfo
    options: dict[str, ProgramOption]

    @classmethod
    def build(cls, raw: dict[str, Any]) -> "Program":
        info = {}
        options = raw.copy()
        for key in ProgramInfo.__required_keys__ | ProgramInfo.__optional_keys__:
            # extract all ProgramInfo keys from 'options' to 'info'
            try:
                info[key] = options[key]
            except LookupError:
                pass
            else:
                del options[key]
        return Program(info=cast(ProgramInfo, info), options=options)


@dataclasses.dataclass(slots=True, kw_only=True)
class AggState:
    zh_mode: int
    device: DeviceStatus
    device_fetched_at: datetime
    notifications: list[PushNotification]
    eco_info: EcoInfo


@dataclasses.dataclass(slots=True, kw_only=True)
class AggUpdateStatus:
    update: UpdateStatus
    ai_fw_version: AiFwVersion
    hh_fw_version: HhFwVersion


@dataclasses.dataclass(slots=True, kw_only=True)
class AggMeta:
    mac_address: str
    model_id: str
    model_name: str
    device_name: str
    serial_number: str
    api_version: tuple[int, ...]

    def create_name(self) -> str:
        if name := self.device_name.strip():
            return name
        return self.model_name or self.model_id or self.serial_number

    def create_unique_name(self) -> str:
        name = self.create_name()
        if self.serial_number in name:
            return name
        return f"{name} ({self.serial_number})"

    def supports_update_status(self) -> bool:
        return self.api_version >= (1, 7, 0)


@dataclasses.dataclass(slots=True, kw_only=True)
class AggCategory:
    key: str
    description: str
    commands: dict[str, Command]


AggConfig = dict[str, AggCategory]


@dataclasses.dataclass(kw_only=True, slots=True)
class Credentials:
    username: str
    password: str


class VZugApi:
    """API client for interacting with V-ZUG appliances.

    This class provides methods to communicate with V-ZUG devices via their
    local network API. It handles authentication, retries, and error handling.

    Args:
        base_url: Base URL of the V-ZUG device (e.g., "http://192.168.1.100")
        credentials: Optional credentials for digest authentication.
                    If None, requests will be unauthenticated.
    """

    @property
    def base_url(self) -> URL:
        """Return the base URL of the V-ZUG device."""
        return self._base_url

    def __init__(
        self,
        base_url: URL | str,
        *,
        credentials: Credentials | None = None,
    ) -> None:
        auth = (
            httpx.DigestAuth(
                username=credentials.username, password=credentials.password
            )
            if credentials
            else None
        )
        transport = httpx.AsyncHTTPTransport(
            verify=False,
            limits=httpx.Limits(max_connections=3, max_keepalive_connections=1),
            retries=5,
        )
        self._client = httpx.AsyncClient(auth=auth, transport=transport)
        self._base_url = URL(base_url)

    async def _command(
        self,
        component: str,
        *,
        command: str,
        params: dict[str, str] | None = None,
        raw: bool = False,
        expected_type: Any = None,
        reject_empty: bool = False,
        attempts: int = 5,
        retry_delay: float = 2.0,
        value_on_err: Callable[[], Any] | None = None,
    ) -> Any:
        """Execute a command on the V-ZUG device API.

        This is the core method for all API interactions. It handles retries,
        error handling, JSON repair, and response validation.

        Args:
            component: API component to call (e.g., "ai" or "hh").
            command: Command name to execute.
            params: Optional query parameters for the command.
            raw: If True, return raw text response instead of parsed JSON.
            expected_type: Expected return type (e.g., dict, list). Raises
                         AssertionError if type doesn't match.
            reject_empty: If True, raise AssertionError on empty responses.
            attempts: Number of retry attempts for failed requests.
            retry_delay: Delay in seconds between retries.
            value_on_err: Optional callback function that returns a default
                         value if all retries fail.

        Returns:
            Parsed response data (dict/list/string) or value from value_on_err
            if all retries failed.

        Raises:
            AuthenticationFailed: If authentication fails (HTTP 401).
            httpx.HTTPStatusError: For non-retryable HTTP errors.
            httpx.TransportError: For network errors after all retries.
            AssertionError: If response type validation fails.
            ValueError: If JSON parsing fails and repair is unsuccessful.
        """
        if params is None:
            params = {}
        final_params = params.copy()
        final_params["command"] = command
        final_params["_"] = str(int(time.time()))

        url = str(self._base_url / component)

        async def once() -> Any:
            _LOGGER.debug(
                "running command %s %s on %s @ %s",
                command,
                params,
                component,
                self._base_url,
            )
            resp = await self._client.get(url, params=final_params)
            resp.raise_for_status()

            if raw:
                content = resp.text
                _LOGGER.debug("raw response: %s", content)
                return content

            try:
                data = resp.json()
            except ValueError:
                if resp.content:
                    _LOGGER.debug("invalid json payload: %s", resp.content)
                    # Try to repair the JSON response before giving up
                    try:
                        repaired_json = json_repair.repair_json(resp.text)
                        data = json.loads(repaired_json)
                        _LOGGER.debug("successfully repaired json: %s", data)
                    except Exception as repair_error:
                        _LOGGER.debug("json repair failed: %s", repair_error)
                        raise  # Re-raise the original ValueError
                else:
                    # we got an empty response, we just treat this as 'None'
                    data = None

            _LOGGER.debug("data: %s", data)
            if expected_type is list and data is None:
                # if we want a list and the response is null, we just treat that as an empty list
                data: Any = []

            if expected_type is not None:
                assert isinstance(data, expected_type), (
                    f"data type mismatch ({type(data)} != {expected_type})"
                )
            if reject_empty:
                assert len(data) > 0, "empty response rejected"
            return data

        last_exc = ValueError("no attempts made")
        attempt_idx = 0
        while attempt_idx < attempts:
            # starts with 0s, then retry_delay
            await asyncio.sleep(attempt_idx * retry_delay)

            try:
                return await once()
            except httpx.HTTPStatusError as err:
                if err.response.status_code == httpx.codes.UNAUTHORIZED:
                    _LOGGER.warning(
                        "Authentication failed for command %s on %s @ %s (attempt %d/%d)",
                        command,
                        component,
                        self._base_url,
                        attempt_idx + 1,
                        attempts,
                    )
                    raise AuthenticationFailed from err
                if not err.response.is_server_error:
                    _LOGGER.warning(
                        "HTTP error %d for command %s on %s @ %s (attempt %d/%d): %s",
                        err.response.status_code,
                        command,
                        component,
                        self._base_url,
                        attempt_idx + 1,
                        attempts,
                        err.response.text[:200] if err.response.text else "No response body",
                    )
                    raise

                last_exc = err
                _LOGGER.debug(
                    "Server error %d for command %s on %s @ %s (attempt %d/%d): %s",
                    err.response.status_code,
                    command,
                    component,
                    self._base_url,
                    attempt_idx + 1,
                    attempts,
                    err.response.text[:200] if err.response.text else "No response body",
                )
            except httpx.TransportError as err:
                last_exc = err
                _LOGGER.debug(
                    "Transport error for command %s on %s @ %s (attempt %d/%d): %r",
                    command,
                    component,
                    self._base_url,
                    attempt_idx + 1,
                    attempts,
                    err,
                )
                continue
            except AssertionError as exc:
                last_exc = exc
                _LOGGER.debug(
                    "Response data assertion failed for command %s on %s @ %s (attempt %d/%d): %s",
                    command,
                    component,
                    self._base_url,
                    attempt_idx + 1,
                    attempts,
                    exc,
                )
            except Exception as exc:
                last_exc = exc
                _LOGGER.debug(
                    "Unknown error for command %s on %s @ %s (attempt %d/%d): %r",
                    command,
                    component,
                    self._base_url,
                    attempt_idx + 1,
                    attempts,
                    exc,
                )

            attempt_idx += 1

        if value_on_err:
            _LOGGER.exception(
                "Command error after %d attempts, using default: %s %s on %s @ %s",
                attempts,
                command,
                params,
                component,
                self._base_url,
                exc_info=last_exc,
            )
            return value_on_err()

        raise last_exc

    async def aggregate_state(self, *, default_on_error: bool = True) -> AggState:
        """Aggregate device state from multiple API endpoints.

        Fetches device status, notifications, and eco info in parallel to
        build a complete state snapshot.

        Args:
            default_on_error: If True, returns empty/default values for failed
                            endpoints instead of raising exceptions.

        Returns:
            AggState containing device status, notifications, and eco info.

        Raises:
            AuthenticationFailed: If authentication credentials are invalid.
        """
        # always start with zh_mode, that seems to do something??
        # zh_mode = await self.get_zh_mode(default_on_error=True)
        zh_mode = -1

        async def _device() -> tuple[DeviceStatus, datetime]:
            data = await self.get_device_status(default_on_error=default_on_error)
            return data, datetime.now(UTC)

        (device, device_fetched_at), notifications, eco_info = await asyncio.gather(
            _device(),
            self.get_last_push_notifications(default_on_error=default_on_error),
            self.get_eco_info(default_on_error=default_on_error),
        )

        return AggState(
            zh_mode=zh_mode,
            device=device,
            device_fetched_at=device_fetched_at,
            notifications=notifications,
            eco_info=eco_info,
        )

    async def aggregate_update_status(
        self, *, supports_update_status: bool, default_on_error: bool = True
    ) -> AggUpdateStatus:
        """Aggregate update status information.

        Fetches update status, AI firmware version, and HH firmware version.

        Args:
            supports_update_status: If True, attempts to fetch update status.
                                  Older devices may not support this endpoint.
            default_on_error: If True, returns empty/default values for failed
                            endpoints instead of raising exceptions.

        Returns:
            AggUpdateStatus containing update information and firmware versions.
        """
        async def _update() -> UpdateStatus:
            if supports_update_status:
                return await self.get_update_status(default_on_error=default_on_error)
            return UpdateStatus()

        update, ai_fw_version, hh_fw_version = await asyncio.gather(
            _update(),
            self.get_ai_fw_version(default_on_error=default_on_error),
            self.get_hh_fw_version(default_on_error=default_on_error),
        )
        return AggUpdateStatus(
            update=update,
            ai_fw_version=ai_fw_version,
            hh_fw_version=hh_fw_version,
        )

    async def aggregate_meta(self, *, default_on_error: bool = False) -> AggMeta:
        """Aggregate device metadata from multiple API endpoints.

        This is typically called during device discovery and setup to gather
        device identification information. Fetches MAC address, device status,
        model description, and firmware versions.

        Args:
            default_on_error: If True, returns empty/default values for failed
                            endpoints. Should typically be False for initial
                            setup to detect connection issues.

        Returns:
            AggMeta containing device identification and metadata.

        Raises:
            AuthenticationFailed: If authentication credentials are invalid.
            httpx.HTTPStatusError: If critical endpoints fail when
                                  default_on_error is False.
        """
        # First method used in config flow to get details about the device
        (
            mac_address,
            device_status,
            model_description,
            ai_firmware,
        ) = await asyncio.gather(
            # This is all from the AI Module/API
            self.get_mac_address(default_on_error=default_on_error),
            self.get_device_status(default_on_error=default_on_error),
            self.get_model_description(default_on_error=default_on_error),
            self.get_ai_fw_version(default_on_error=default_on_error),
        )

        try:
            # Only supported on some devices, probably with newer hh module
            device_info = await self.get_device_info(default_on_error=True)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == httpx.codes.NOT_FOUND:
                # Device does not support this, so we just use the AI data
                device_info = None
            else:
                raise

        if device_info:
            raw_api_version = device_info.get("apiVersion", "")
            hh_api_version = tuple(map(int, (raw_api_version.split("."))))

            return AggMeta(
                mac_address=mac_address,
                model_id=device_info.get("model", ""),
                model_name=device_info.get("description", ""),
                device_name=device_info.get("name", ""),
                serial_number=device_info.get("serialNumber", ""),
                api_version=hh_api_version,
            )
        else:
            raw_api_version = ai_firmware.get("apiVersion", "")
            ai_api_version = tuple(map(int, (raw_api_version.split("."))))

            return AggMeta(
                mac_address=mac_address,
                model_id="",
                model_name=model_description,
                device_name=device_status.get("DeviceName", ""),
                serial_number=device_status.get("Serial", ""),
                api_version=ai_api_version,
            )

    async def aggregate_config(self) -> AggConfig:
        """Aggregate device configuration tree.

        Discovers all available categories and commands by querying the device's
        configuration endpoints. This builds a complete tree of configurable
        settings that can be exposed as Home Assistant entities.

        Returns:
            AggConfig dictionary mapping category keys to AggCategory objects,
            each containing available commands.

        Raises:
            AuthenticationFailed: If authentication credentials are invalid.
            httpx.HTTPStatusError: If configuration endpoints are unavailable.
        """
        category_keys = await self.list_categories()
        config_tree: AggConfig = {}
        for category_key in category_keys:
            category_raw, command_keys = await asyncio.gather(
                self.get_category(category_key),
                self.list_commands(category_key),
            )
            category = AggCategory(
                key=category_key,
                description=category_raw.get("description", ""),
                commands={},
            )

            async def handle_command_key(command_key: str) -> None:
                command_raw = await self.get_command(command_key)
                category.commands[command_key] = command_raw

            await asyncio.gather(
                *(handle_command_key(command_key) for command_key in command_keys)
            )
            config_tree[category_key] = category
        return config_tree

    async def get_mac_address(self, *, default_on_error: bool = False) -> str:
        """Get the MAC address of the device.

        Args:
            default_on_error: If True, returns empty string on error.

        Returns:
            MAC address as string (format: "XX:XX:XX:XX:XX:XX" or "XX-XX-XX-XX-XX-XX").
        """
        return await self._command(
            "ai",
            command="getMacAddress",
            raw=True,
            value_on_err=(lambda: "") if default_on_error else None,
        )

    async def get_model_description(self, *, default_on_error: bool = False) -> str:
        return await self._command(
            "ai",
            command="getModelDescription",
            raw=True,
            value_on_err=(lambda: "") if default_on_error else None,
        )

    async def get_device_status(
        self, *, default_on_error: bool = False
    ) -> DeviceStatus:
        return await self._command(
            "ai",
            command="getDeviceStatus",
            expected_type=dict,
            value_on_err=(lambda: DeviceStatus()) if default_on_error else None,
        )

    async def get_update_status(
        self, *, default_on_error: bool = False
    ) -> UpdateStatus:
        return await self._command(
            "ai",
            command="getUpdateStatus",
            expected_type=dict,
            value_on_err=(lambda: UpdateStatus()) if default_on_error else None,
        )

    async def check_for_updates(self) -> None:
        await self._command(
            "ai",
            command="checkUpdate",
            raw=True,
            attempts=2,
        )

    async def do_ai_update(self) -> None:
        await self._command("ai", command="doAIUpdate")

    async def do_hhg_update(self) -> None:
        await self._command("ai", command="doHHGUpdate")

    async def get_last_push_notifications(
        self, *, default_on_error: bool = False
    ) -> list[PushNotification]:
        return await self._command(
            "ai",
            command="getLastPUSHNotifications",
            expected_type=list,
            value_on_err=(lambda: []) if default_on_error else None,
        )

    async def list_categories(self) -> list[str]:
        return await self._command(
            "hh",
            command="getCategories",
            expected_type=list,
            # the API sometimes wrongly returns an empty list, but there are also appliances (ex. AdoraWash V4000) which don't have any categories
            reject_empty=False,
        )

    async def get_category(self, value: str) -> Category:
        return await self._command(
            "hh", command="getCategory", params={"value": value}, expected_type=dict
        )

    async def list_commands(self, value: str) -> list[str]:
        return await self._command(
            "hh", command="getCommands", params={"value": value}, expected_type=list
        )

    async def get_command(self, value: str) -> Command:
        return await self._command(
            "hh", command="getCommand", params={"value": value}, expected_type=dict
        )

    async def set_command(self, command: str, value: str) -> None:
        await self._command(
            "hh",
            command=f"set{command}",
            params={"value": value},
            raw=True,
            attempts=2,
        )

    async def do_command_action(self, command: str) -> None:
        await self._command(
            "hh",
            command=f"do{command}",
            raw=True,
            attempts=2,
        )

    async def get_hh_fw_version(self, *, default_on_error: bool = False) -> HhFwVersion:
        return await self._command(
            "hh",
            command="getFWVersion",
            expected_type=dict,
            value_on_err=(lambda: HhFwVersion()) if default_on_error else None,
        )

    async def get_ai_fw_version(self, *, default_on_error: bool = False) -> AiFwVersion:
        return await self._command(
            "ai",
            command="getFWVersion",
            expected_type=dict,
            value_on_err=(lambda: AiFwVersion()) if default_on_error else None,
        )

    async def get_zh_mode(self, *, default_on_error: bool = False) -> int:
        data = await self._command(
            "hh",
            command="getZHMode",
            expected_type=dict,
            value_on_err=(lambda: {"value": -1}) if default_on_error else None,
        )
        return data["value"]

    async def get_eco_info(self, *, default_on_error: bool = False) -> EcoInfo:
        """Get energy and water consumption information.

        Returns eco metrics including total consumption, averages, and
        program-specific consumption. If both water and energy totals are 0,
        returns an empty EcoInfo to indicate no data is available.

        Args:
            default_on_error: If True, returns empty EcoInfo on error.

        Returns:
            EcoInfo containing water and energy metrics, or empty dict if
            no meaningful data is available.
        """
        result = await self._command(
            "hh",
            command="getEcoInfo",
            expected_type=dict,
            value_on_err=(lambda: EcoInfo()) if default_on_error else None,
        )

        water_total = result.get("water", {}).get("total", 0)
        energy_total = result.get("energy", {}).get("total", 0)

        # If both water and energy totals are 0, we return an empty EcoInfo
        # This is to handle cases where the API returns 0s for both metrics
        if water_total == 0 and energy_total == 0:
            return EcoInfo()

        return result

    async def get_device_info(self, *, default_on_error: bool = False) -> DeviceInfo:
        # 'getAPIVersion' can be used to get only the API version
        # 'getZHMode' gives just the zh mode
        return await self._command(
            "hh",
            command="getDeviceInfo",
            expected_type=dict,
            value_on_err=(lambda: DeviceInfo()) if default_on_error else None,
        )

    async def get_program(self) -> list[Program]:
        """Get current program information.

        Retrieves detailed information about the currently selected program
        including available options, settings, and configuration.

        Returns:
            List of Program objects containing program details and options.

        Note:
            This endpoint is only supported on certain devices (see API
            compatibility table in CONTRIBUTING.md).

        Raises:
            httpx.HTTPStatusError: If endpoint is not supported (404) or other error.
        """
        # TODO: this is interesting but what can we do with it??
        # [{"id":52,"name":"Alltag Kurz","status":"selected","starttime":{"min":0,"max":86400,"step":600},"duration":{"set":2460}, "energySaving":{"set":false,"options":[true,false]},"optiStart":{"set":false},"steamfinish":{"set":false,"options":[true,false]},"partialload":{"set":false,"options":[true,false]},"rinsePlus":{"set":false,"options":[true,false]},"dryPlus":{"set":false,"options":[true,false]},"stepIds":[82,81,82,79,78,76,73,74,75,72,71,70]}]
        # [{"id":50,"name":"Eco",        "status":"selected","starttime":{"min":0,"max":86400,"step":600},"duration":{"set":22440},"energySaving":{"set":false,"options":[true,false]},"optiStart":{"set":false},"steamfinish":{"set":true, "options":[true,false]},"partialload":{"set":false,"options":[true,false]},"rinsePlus":{"set":false,"options":[true,false]},"dryPlus":{"set":false,"options":[true,false]},"stepIds":[79,81,79,78,74,75,72,70]}]
        raw_programs: list[dict[str, Any]] = await self._command(
            "hh",
            command="getProgram",
            expected_type=list,
        )
        return [Program.build(raw) for raw in raw_programs]

    async def set_program(
        self, program_id: int, options: dict[str, Any] | None = None
    ) -> list[Any]:
        """Set program configuration.

        Configures and optionally starts a program on the device.

        Args:
            program_id: ID of the program to set (from getAllProgramIds).
            options: Optional program options dictionary. Can include:
                    - Program option flags (e.g., "steamfinish", "energySaving")
                    - Start time configuration
                    - Duration settings
                    If None, only the program ID is set.

        Returns:
            Raw response from the device (usually confirmation).

        Note:
            This endpoint is only supported on certain devices (see API
            compatibility table in CONTRIBUTING.md).

        Raises:
            httpx.HTTPStatusError: If endpoint is not supported or invalid parameters.
        """
        # example options: {"id":50,"dryPlus":false,"energySaving":false,"partialload":false,"rinsePlus":false,"steamfinish":true}
        # also seen with just the "id" key
        if not options:
            options = {}
        options["id"] = program_id
        return await self._command(
            "hh",
            command="setProgram",
            params={"value": json.dumps(options)},
            raw=True,
            attempts=2,
        )

    async def get_all_program_ids(self) -> list[int]:
        """Get list of all available program IDs.

        Returns all program IDs that can be used with setProgram. Note that
        program names are not included - they must be retrieved via getProgram
        for each individual program.

        Returns:
            List of program IDs as integers.

        Note:
            This endpoint is only supported on certain devices (see API
            compatibility table in CONTRIBUTING.md).

        Raises:
            httpx.HTTPStatusError: If endpoint is not supported.
        """
        # TODO: this gives us a nice list of ids that could be used with set_program, but we need a program id to name mapping
        return await self._command(
            "hh",
            command="getAllProgramIds",
            expected_type=list,
        )


class AuthenticationFailed(Exception):
    """Exception raised when authentication with the device fails.

    This typically indicates invalid credentials or that the device requires
    authentication but none was provided.
    """
