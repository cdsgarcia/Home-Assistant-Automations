"""JBD BMS Config: read/write JBD (Gentai) BMS EEPROM settings over BLE via services.

The BMS accepts one BLE connection, so each call temporarily unloads the pack's BMS_BLE
config entry, does the work, then sets it up again.
"""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .protocol import SETTINGS, UUID_NOTIFY, UUID_WRITE, JbdSession

_LOGGER = logging.getLogger(__name__)
DOMAIN = "jbd_config"
BMS_BLE_DOMAIN = "bms_ble"

PACK_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required("address"): vol.All(cv.string, vol.Upper),
        vol.Optional("password", default="123456"): vol.All(cv.string, vol.Length(min=6, max=6)),
    }
)
CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Required("packs"): vol.All(cv.ensure_list, [PACK_SCHEMA])})},
    extra=vol.ALLOW_EXTRA,
)


def _norm(mac: str) -> str:
    return mac.replace(":", "").replace("-", "").upper()


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    packs = {p["name"]: p for p in config[DOMAIN]["packs"]}
    locks = {name: asyncio.Lock() for name in packs}

    def _bms_ble_entry(pack: dict):
        for entry in hass.config_entries.async_entries(BMS_BLE_DOMAIN):
            addr = entry.data.get("address") or entry.unique_id or ""
            if _norm(addr) == _norm(pack["address"]) or entry.title == pack["name"]:
                return entry
        return None

    async def _session(pack: dict, changes: dict[str, int]) -> dict[str, int]:
        device = bluetooth.async_ble_device_from_address(hass, pack["address"], connectable=True)
        if device is None:
            raise HomeAssistantError(f"{pack['name']}: {pack['address']} not in Bluetooth range")
        client = await establish_connection(
            BleakClientWithServiceCache, device, pack["name"], max_attempts=4
        )
        try:
            async def write(data: bytes) -> None:
                await client.write_gatt_char(UUID_WRITE, data, response=False)

            s = JbdSession(write, pack["address"])
            await client.start_notify(UUID_NOTIFY, s.on_notify)
            await s.authenticate(pack["password"])
            return await s.factory(changes)
        finally:
            await client.disconnect()

    async def _run(name: str, changes: dict[str, int]) -> dict:
        pack = packs[name]
        async with locks[name]:
            entry = _bms_ble_entry(pack)
            unloaded = False
            if entry and entry.state is ConfigEntryState.LOADED:
                await hass.config_entries.async_unload(entry.entry_id)
                unloaded = True
                await asyncio.sleep(2)
            try:
                before = await _session(pack, {})
                result: dict = {"before": before}
                todo = {k: v for k, v in changes.items() if before.get(k) != v}
                if todo:
                    await _session(pack, todo)
                    await asyncio.sleep(2)
                    after = await _session(pack, {})  # fresh session proves it was saved
                    result["after"] = after
                    result["verified"] = all(after[k] == v for k, v in todo.items())
                    _LOGGER.warning("%s: wrote %s, read back %s", name, todo, after)
                elif changes:
                    result["verified"] = True
                    result["note"] = "already set, nothing written"
                return result
            except (BleakError, TimeoutError, OSError, PermissionError) as err:
                raise HomeAssistantError(f"{name}: {err}") from err
            finally:
                if unloaded:
                    await hass.config_entries.async_setup(entry.entry_id)

    def _pack_names(call: ServiceCall) -> list[str]:
        names = call.data.get("pack") or list(packs)
        unknown = [n for n in names if n not in packs]
        if unknown:
            raise ServiceValidationError(f"Unknown pack(s) {unknown}; configured: {list(packs)}")
        return names

    async def read_config(call: ServiceCall) -> ServiceResponse:
        return {n: await _run(n, {}) for n in _pack_names(call)}

    async def set_config(call: ServiceCall) -> ServiceResponse:
        changes = {k: call.data[k] for k in SETTINGS if k in call.data}
        if not changes:
            raise ServiceValidationError("Give at least one setting to change")
        return {n: await _run(n, changes) for n in _pack_names(call)}

    pack_field = {vol.Optional("pack"): vol.All(cv.ensure_list, [cv.string])}
    hass.services.async_register(
        DOMAIN, "read_config", read_config,
        schema=vol.Schema(pack_field), supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, "set_config", set_config,
        schema=vol.Schema(
            {
                **pack_field,
                **{vol.Optional(k): vol.All(vol.Coerce(int), vol.Range(lo, hi)) for k, (_, lo, hi) in SETTINGS.items()},
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True
