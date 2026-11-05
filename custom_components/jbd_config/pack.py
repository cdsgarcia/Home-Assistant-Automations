"""One JBD pack: BLE session handling and the last known settings."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .protocol import SETTINGS, UUID_NOTIFY, UUID_WRITE, JbdSession

_LOGGER = logging.getLogger(__name__)
BMS_BLE_DOMAIN = "bms_ble"


def norm_mac(mac: str) -> str:
    return mac.replace(":", "").replace("-", "").upper()


class JbdPack:
    def __init__(self, hass: HomeAssistant, name: str, address: str, password: str) -> None:
        self.hass, self.name, self.address, self.password = hass, name, address.upper(), password
        self.values: dict[str, int] = {}
        self.lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []

    @callback
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def _update(self, values: dict) -> None:
        self.values.update({k: v for k, v in values.items() if k in SETTINGS})
        for cb in list(self._listeners):
            cb()

    def _bms_ble_entry(self):
        for entry in self.hass.config_entries.async_entries(BMS_BLE_DOMAIN):
            addr = entry.data.get("address") or entry.unique_id or ""
            if norm_mac(addr) == norm_mac(self.address):
                return entry
        return None

    async def _connect(self):
        device = None
        for _ in range(15):  # just after a disconnect the pack may not be advertising yet
            device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
            if device:
                break
            await asyncio.sleep(2)
        if device is None:
            raise HomeAssistantError(f"{self.name}: {self.address} not in Bluetooth range")
        client = await establish_connection(BleakClientWithServiceCache, device, self.name, max_attempts=4)
        if client.services.get_characteristic(UUID_WRITE) is None:
            # Stale GATT cache: clear and reconnect once.
            await client.clear_cache()
            await client.disconnect()
            client = await establish_connection(
                BleakClientWithServiceCache, device, self.name, max_attempts=4, use_services_cache=False
            )
            if client.services.get_characteristic(UUID_WRITE) is None:
                await client.disconnect()
                raise HomeAssistantError(f"{self.name}: JBD write characteristic not found")
        return client

    async def _session(self, changes: dict[str, int], raw: tuple[int, ...] = ()) -> dict:
        for attempt in (1, 2):
            client = await self._connect()
            try:
                async def write(data: bytes) -> None:
                    await client.write_gatt_char(UUID_WRITE, data, response=False)

                s = JbdSession(write, self.address)
                await client.start_notify(UUID_NOTIFY, s.on_notify)
                auth = await s.authenticate(self.password)
                result = await s.factory(changes, raw)
                result["auth"] = auth
                return result
            except BleakError as err:
                if attempt == 2 or changes:  # don't blindly repeat a write
                    raise
                _LOGGER.debug("%s: retrying after %s", self.name, err)
            finally:
                await client.disconnect()
        raise HomeAssistantError("unreachable")

    async def run(self, changes: dict[str, int] | None = None, raw: tuple[int, ...] = ()) -> dict:
        """Read settings and optionally write `changes`, with BMS_BLE paused meanwhile."""
        changes = changes or {}
        async with self.lock:
            entry = self._bms_ble_entry()
            unloaded = False
            if entry and entry.state is ConfigEntryState.LOADED:
                await self.hass.config_entries.async_unload(entry.entry_id)
                unloaded = True
                await asyncio.sleep(2)
            try:
                before = await self._session({}, raw)
                result: dict = {"before": before}
                self._update(before)
                todo = {k: v for k, v in changes.items() if before.get(k) != v}
                if todo:
                    result["in_session"] = await self._session(todo)
                    await asyncio.sleep(2)
                    after = await self._session({})  # fresh session proves it was saved
                    result["after"] = after
                    result["verified"] = all(after.get(k) == v for k, v in todo.items())
                    self._update(after)
                    _LOGGER.warning("%s: wrote %s -> %s", self.name, todo, result)
                elif changes:
                    result["verified"] = True
                return result
            except (BleakError, TimeoutError, OSError, PermissionError) as err:
                raise HomeAssistantError(f"{self.name}: {err}") from err
            finally:
                if unloaded:
                    await self.hass.config_entries.async_setup(entry.entry_id)
