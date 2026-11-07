"""JBD BMS Config: view and change JBD (Gentai) BMS EEPROM settings from Home Assistant.

Each pack is a config entry whose entities (number + button) join the pack's BMS_BLE device.
The BMS accepts one BLE connection, so every read/write briefly unloads the BMS_BLE entry.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_ADDRESS, CONF_PASSWORD, DEFAULT_PASSWORD, DOMAIN
from .pack import JbdPack
from .protocol import BALANCE_MODES, ESUP_BLOCKS, SETTINGS

PLATFORMS = [Platform.BUTTON, Platform.NUMBER, Platform.SELECT]

PACK_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required(CONF_ADDRESS): vol.All(cv.string, vol.Upper),
        vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): vol.All(cv.string, vol.Length(min=6, max=6)),
    }
)
# Optional legacy YAML; packs listed here are imported as UI config entries.
CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Required("packs"): vol.All(cv.ensure_list, [PACK_SCHEMA])})},
    extra=vol.ALLOW_EXTRA,
)


def _packs(hass: HomeAssistant) -> dict[str, JbdPack]:
    return {p.name: p for p in hass.data.get(DOMAIN, {}).values()}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})
    for pack in config.get(DOMAIN, {}).get("packs", []):
        hass.async_create_task(
            hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_IMPORT}, data=pack)
        )

    def _selected(call: ServiceCall) -> list[JbdPack]:
        packs = _packs(hass)
        names = call.data.get("pack") or list(packs)
        unknown = [n for n in names if n not in packs]
        if unknown:
            raise ServiceValidationError(f"Unknown pack(s) {unknown}; configured: {list(packs)}")
        return [packs[n] for n in names]

    async def read_config(call: ServiceCall) -> ServiceResponse:
        raw = tuple(call.data.get("registers", ()))
        return {p.name: await p.run(raw=raw) for p in _selected(call)}

    async def set_config(call: ServiceCall) -> ServiceResponse:
        changes = {k: call.data[k] for k in SETTINGS if k in call.data}
        if not changes:
            raise ServiceValidationError("Give at least one setting to change")
        return {p.name: await p.run(changes) for p in _selected(call)}

    async def esup_read(call: ServiceCall) -> ServiceResponse:
        addrs = tuple(call.data.get("addresses") or ()) or None
        blocks = tuple(call.data.get("blocks") or ESUP_BLOCKS)
        return {p.name: await p.esup_read(addrs, blocks) for p in _selected(call)}

    async def esup_set(call: ServiceCall) -> ServiceResponse:
        writes = {
            k: round(call.data[f] * 100)
            for k, f in (("full_adjust_v_x100", "full_adjust_v"), ("full_adjust_c_x100", "full_adjust_c"))
            if f in call.data
        }
        if "balance_mode" in call.data:
            writes["balance_mode"] = BALANCE_MODES[call.data["balance_mode"]]
        if not writes:
            raise ServiceValidationError("Give full_adjust_v, full_adjust_c and/or balance_mode")
        addrs = tuple(call.data.get("addresses") or ()) or None
        return {p.name: await p.esup_set(addrs, writes) for p in _selected(call)}

    pack_field = {vol.Optional("pack"): vol.All(cv.ensure_list, [cv.string])}
    addr_field = {
        vol.Optional("addresses"): vol.All(cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(0, 0x1F))])
    }
    hass.services.async_register(
        DOMAIN, "esup_read", esup_read,
        schema=vol.Schema(
            {
                **pack_field,
                **addr_field,
                vol.Optional("blocks"): vol.All(cv.ensure_list, [vol.In(list(ESUP_BLOCKS))]),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, "esup_set", esup_set,
        schema=vol.Schema(
            {
                **pack_field,
                **addr_field,
                vol.Optional("full_adjust_v"): vol.All(vol.Coerce(float), vol.Range(52.0, 58.4)),
                vol.Optional("full_adjust_c"): vol.All(vol.Coerce(float), vol.Range(0.5, 30.0)),
                vol.Optional("balance_mode"): vol.In(list(BALANCE_MODES)),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "read_config", read_config,
        schema=vol.Schema(
            {**pack_field, vol.Optional("registers"): vol.All(cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(0x10, 0xFF))])}
        ),
        supports_response=SupportsResponse.ONLY,
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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = JbdPack(
        hass, entry.title, entry.data[CONF_ADDRESS], entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD)
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok
