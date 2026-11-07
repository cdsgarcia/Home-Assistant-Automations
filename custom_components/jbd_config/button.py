"""Button that reads the current settings from the BMS."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .number import device_info
from .pack import JbdPack, norm_mac


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    pack = hass.data[DOMAIN][entry.entry_id]
    add([JbdReadButton(pack), JbdEsupReadButton(pack)])


class JbdReadButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Read BMS settings"
    _attr_icon = "mdi:download"

    def __init__(self, pack: JbdPack) -> None:
        self._pack = pack
        self._attr_unique_id = f"{norm_mac(pack.address)}_read_settings"
        self._attr_device_info = device_info(pack)

    async def async_press(self) -> None:
        await self._pack.run()


class JbdEsupReadButton(JbdReadButton):
    _attr_name = "Read ES-UP settings"
    _attr_icon = "mdi:battery-sync"

    def __init__(self, pack: JbdPack) -> None:
        super().__init__(pack)
        self._attr_unique_id = f"{norm_mac(pack.address)}_read_esup"

    async def async_press(self) -> None:
        await self._pack.esup_read(None, ("user_params",))
