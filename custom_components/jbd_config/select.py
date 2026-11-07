"""Select entity for the ES-UP balance mode (charge-only vs static). Changing it writes to the BMS."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .number import device_info
from .pack import JbdPack, norm_mac
from .protocol import BALANCE_MODES

KEY = "balance_mode"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([JbdBalanceModeSelect(hass.data[DOMAIN][entry.entry_id])])


class JbdBalanceModeSelect(SelectEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Balance mode (ES-UP)"
    _attr_icon = "mdi:scale-balance"
    _attr_options = list(BALANCE_MODES)

    def __init__(self, pack: JbdPack) -> None:
        self._pack = pack
        self._attr_unique_id = f"{norm_mac(pack.address)}_{KEY}"
        self._attr_device_info = device_info(pack)

    @property
    def current_option(self) -> str | None:
        raw = self._pack.values.get(KEY)
        return next((k for k, v in BALANCE_MODES.items() if v == raw), None)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if KEY not in self._pack.values and (last := await self.async_get_last_state()):
            if last.state in BALANCE_MODES:
                self._pack.values[KEY] = BALANCE_MODES[last.state]
        self.async_on_remove(self._pack.add_listener(self.async_write_ha_state))

    async def async_select_option(self, option: str) -> None:
        result = await self._pack.esup_set(None, {KEY: BALANCE_MODES[option]})
        if not result.get("verified"):
            raise HomeAssistantError(
                f"{self._pack.name}: BMS accepted {option} but kept {result.get('after', {}).get(KEY)}"
            )
