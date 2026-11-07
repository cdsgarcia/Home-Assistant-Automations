"""Number entities for the JBD EEPROM settings. Changing one writes it to the BMS."""
from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfElectricPotential
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .pack import JbdPack, norm_mac
from .protocol import SETTINGS

NAMES = {
    "cell_full_mv": "Cell full voltage",
    "balance_start_mv": "Balance start voltage",
    "balance_delta_mv": "Balance delta",
}
# ES-UP params stored ×100; key: (name, unit, min, max) in V / A.
ESUP_NUMBERS = {
    "full_adjust_v_x100": ("Full charge voltage (ES-UP)", UnitOfElectricPotential.VOLT, 52.0, 58.4),
    "full_adjust_c_x100": ("Full charge current (ES-UP)", UnitOfElectricCurrent.AMPERE, 0.5, 30.0),
}


def device_info(pack: JbdPack) -> DeviceInfo:
    # Matches the BMS_BLE device by its Bluetooth connection, so entities show on that device.
    return DeviceInfo(connections={(CONNECTION_BLUETOOTH, pack.address)})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    pack: JbdPack = hass.data[DOMAIN][entry.entry_id]
    add([*(JbdSettingNumber(pack, key) for key in SETTINGS), *(JbdEsupNumber(pack, key) for key in ESUP_NUMBERS)])


class JbdSettingNumber(RestoreNumber):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfElectricPotential.MILLIVOLT
    _attr_icon = "mdi:tune-vertical"

    def __init__(self, pack: JbdPack, key: str) -> None:
        self._pack, self._key = pack, key
        _, self._attr_native_min_value, self._attr_native_max_value = SETTINGS[key]
        self._attr_name = NAMES[key]
        self._attr_unique_id = f"{norm_mac(pack.address)}_{key}"
        self._attr_device_info = device_info(pack)

    @property
    def native_value(self) -> float | None:
        return self._pack.values.get(self._key)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._key not in self._pack.values and (last := await self.async_get_last_number_data()):
            if last.native_value is not None:
                self._pack.values[self._key] = int(last.native_value)
        self.async_on_remove(self._pack.add_listener(self.async_write_ha_state))

    async def async_set_native_value(self, value: float) -> None:
        result = await self._pack.run({self._key: int(value)})
        if not result.get("verified"):
            got = result.get("after", {}).get(self._key)
            raise HomeAssistantError(
                f"{self._pack.name}: BMS accepted {int(value)} but kept {got} {self.unit_of_measurement}"
            )


class JbdEsupNumber(JbdSettingNumber):
    """ES-UP FullAdjustV/C: the real 100% SOC reset point on UP16S firmware (DD reg 0x12 is ignored)."""

    _attr_native_step = 0.01
    _attr_icon = "mdi:battery-sync"

    def __init__(self, pack: JbdPack, key: str) -> None:
        self._pack, self._key = pack, key
        name, self._attr_native_unit_of_measurement, self._attr_native_min_value, self._attr_native_max_value = (
            ESUP_NUMBERS[key]
        )
        self._attr_name = name
        self._attr_unique_id = f"{norm_mac(pack.address)}_{key}"
        self._attr_device_info = device_info(pack)

    @property
    def native_value(self) -> float | None:
        raw = self._pack.values.get(self._key)
        return None if raw is None else raw / 100

    async def async_added_to_hass(self) -> None:
        await RestoreNumber.async_added_to_hass(self)
        if self._key not in self._pack.values and (last := await self.async_get_last_number_data()):
            if last.native_value is not None:
                self._pack.values[self._key] = round(last.native_value * 100)
        self.async_on_remove(self._pack.add_listener(self.async_write_ha_state))

    async def async_set_native_value(self, value: float) -> None:
        raw = round(value * 100)
        result = await self._pack.esup_set(None, {self._key: raw})
        if not result.get("verified"):
            got = result.get("after", {}).get(self._key)
            raise HomeAssistantError(
                f"{self._pack.name}: BMS accepted {value} but kept "
                f"{None if got is None else got / 100} {self.unit_of_measurement}"
            )
