"""Config flow: pick a BMS_BLE pack (JBD) and its BLE password."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import SelectOptionDict, SelectSelector, SelectSelectorConfig

from .const import CONF_ADDRESS, CONF_ESUP_ADDRESS, CONF_PASSWORD, DEFAULT_PASSWORD, DOMAIN
from .pack import BMS_BLE_DOMAIN, norm_mac


class JbdConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _create(
        self, name: str, address: str, password: str, esup_address: int | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(norm_mac(address))
        # Re-importing YAML updates the pinned ES-UP address of an existing entry (and reloads it).
        self._abort_if_unique_id_configured(
            updates={CONF_ESUP_ADDRESS: esup_address} if esup_address is not None else None
        )
        data = {CONF_ADDRESS: address.upper(), CONF_PASSWORD: password}
        if esup_address is not None:
            data[CONF_ESUP_ADDRESS] = esup_address
        return self.async_create_entry(title=name, data=data)

    async def async_step_import(self, data: dict[str, Any]) -> ConfigFlowResult:
        return await self._create(
            data["name"], data[CONF_ADDRESS], data[CONF_PASSWORD], data.get(CONF_ESUP_ADDRESS)
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        packs = {}
        for e in self.hass.config_entries.async_entries(BMS_BLE_DOMAIN):
            addr = (e.data.get("address") or e.unique_id or "").upper()
            if addr:
                packs[addr] = e.title
        configured = {e.unique_id for e in self._async_current_entries()}
        options = [
            SelectOptionDict(value=a, label=f"{t} ({a})")
            for a, t in sorted(packs.items(), key=lambda x: x[1])
            if norm_mac(a) not in configured
        ]
        errors: dict[str, str] = {}
        if user_input is not None:
            if len(user_input[CONF_PASSWORD]) != 6:
                errors[CONF_PASSWORD] = "password_length"
            else:
                addr = user_input[CONF_ADDRESS].upper()
                esup = user_input.get(CONF_ESUP_ADDRESS)
                return await self._create(
                    packs.get(addr, addr), addr, user_input[CONF_PASSWORD], int(esup) if esup is not None else None
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): SelectSelector(
                        SelectSelectorConfig(options=options, custom_value=True)
                    ),
                    vol.Required(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
                    vol.Optional(CONF_ESUP_ADDRESS): vol.All(vol.Coerce(int), vol.Range(0, 0x1F)),
                }
            ),
            errors=errors,
        )
