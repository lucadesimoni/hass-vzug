from homeassistant.const import EntityCategory
from homeassistant.helpers.typing import UndefinedType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import api
from .shared import ConfigCoordinator, Shared


class UserConfigEntity(CoordinatorEntity[ConfigCoordinator]):
    """Base class for entities representing user-configurable device settings.

    These entities are dynamically created based on the device's configuration
    tree. The entity category (CONFIG vs DIAGNOSTIC) is determined by whether
    the command is alterable.

    Attributes:
        shared: Shared coordinator and state for the device.
        vzug_category_key: Category key in the device configuration.
        vzug_command_key: Command key within the category.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, shared: Shared, *, category_key: str, command_key: str) -> None:
        """Initialize a user config entity.

        Args:
            shared: Shared coordinator and state for the device.
            category_key: Category key in the device configuration.
            command_key: Command key within the category.
        """
        super().__init__(shared.config_coord)
        self.shared = shared
        self.vzug_category_key = category_key
        self.vzug_command_key = command_key

        self._attr_unique_id = (
            f"{shared.unique_id_prefix}-userconfig-{category_key}-{command_key}"
        )
        self._attr_device_info = shared.device_info
        self._attr_extra_state_attributes = {
            "category_key": category_key,
            "command_key": command_key,
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
        )

    @property
    def vzug_command(self) -> api.Command:
        try:
            return self.coordinator.data[self.vzug_category_key].commands[
                self.vzug_command_key
            ]
        except LookupError:
            return api.Command()

    @property
    def name(self) -> str | UndefinedType | None:
        name = self.vzug_command.get("description")
        if not name:
            name = self.vzug_command_key
        return name

    @property
    def entity_category(self) -> EntityCategory | None:
        return (
            EntityCategory.CONFIG
            if self.vzug_command.get("alterable", False)
            else EntityCategory.DIAGNOSTIC
        )
