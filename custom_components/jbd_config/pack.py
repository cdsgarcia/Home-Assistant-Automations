"""One JBD pack: BLE session handling and the last known settings."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
import logging

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .protocol import (
    ESUP_BLOCKS,
    ESUP_WRITABLE,
    SETTINGS,
    UUID_NOTIFY,
    UUID_WRITE,
    JbdSession,
    esup_decode,
)

_LOGGER = logging.getLogger(__name__)
BMS_BLE_DOMAIN = "bms_ble"


def norm_mac(mac: str) -> str:
    return mac.replace(":", "").replace("-", "").upper()


class JbdPack:
    def __init__(self, hass: HomeAssistant, name: str, address: str, password: str) -> None:
        self.hass, self.name, self.address, self.password = hass, name, address.upper(), password
        self.values: dict[str, int] = {}
        self.esup_addr: int | None = None
        self.lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []

    @callback
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def _update(self, values: dict) -> None:
        self.values.update({k: v for k, v in values.items() if k in SETTINGS or k in ESUP_WRITABLE})
        for cb in list(self._listeners):
            cb()

    def _esup_addrs(self, addrs: tuple[int, ...] | None) -> tuple[int, ...]:
        if addrs:
            return addrs
        # Address = parallel DIP address (M1=1, S1=2, S2=3); the last one found is tried first.
        default = (1, 2, 3, 4, 0)
        return default if self.esup_addr is None else (self.esup_addr, *(a for a in default if a != self.esup_addr))

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

    async def _esup_session(
        self, addrs: tuple[int, ...], blocks: tuple[str, ...], writes: dict[str, int] | None = None
    ) -> dict:
        """Find the ES-UP address, read `blocks` (user_params first if writing), then write changed `writes`."""
        client = await self._connect()
        try:
            async def write(data: bytes) -> None:
                await client.write_gatt_char(UUID_WRITE, data, response=False)

            s = JbdSession(write, self.address)
            await client.start_notify(UUID_NOTIFY, s.on_notify)
            out: dict = {"auth": await s.authenticate(self.password)}
            addr = None
            for a in addrs:  # find the address the BMS answers on
                start, end = ESUP_BLOCKS[blocks[0]]
                try:
                    data = await s.esup_read(a, start, end)
                except TimeoutError:
                    continue
                addr = a
                out[blocks[0]] = {"hex": data.hex(), **esup_decode(blocks[0], data)}
                break
            out["addr"] = addr
            if addr is None:
                out["error"] = f"no ES-UP reply on addresses {list(addrs)}"
                return out
            for b in blocks[1:]:
                start, end = ESUP_BLOCKS[b]
                try:
                    data = await s.esup_read(addr, start, end)
                    out[b] = {"hex": data.hex(), **esup_decode(b, data)}
                except (TimeoutError, OSError) as err:
                    out[b] = {"error": str(err)}
            if writes:
                cur = out[blocks[0]]
                todo = {k: v for k, v in writes.items() if cur.get(k) != v}
                out["writes"] = {
                    k: {"value": v, "ack": await s.esup_write(addr, ESUP_WRITABLE[k], v)} for k, v in todo.items()
                }
            return out
        finally:
            await client.disconnect()

    @asynccontextmanager
    async def _bms_ble_paused(self):
        entry = self._bms_ble_entry()
        unloaded = False
        if entry and entry.state is ConfigEntryState.LOADED:
            await self.hass.config_entries.async_unload(entry.entry_id)
            unloaded = True
            await asyncio.sleep(2)
        try:
            yield
        except (BleakError, TimeoutError, OSError, PermissionError) as err:
            raise HomeAssistantError(f"{self.name}: {err}") from err
        finally:
            if unloaded:
                await self.hass.config_entries.async_setup(entry.entry_id)

    async def esup_read(self, addrs: tuple[int, ...] | None, blocks: tuple[str, ...]) -> dict:
        """Read-only ES-UP query (nothing is written), with BMS_BLE paused meanwhile."""
        async with self.lock, self._bms_ble_paused():
            out = await self._esup_session(self._esup_addrs(addrs), blocks)
        if out.get("addr") is not None:
            self.esup_addr = out["addr"]
        if isinstance(out.get("user_params"), dict):
            self._update(out["user_params"])
        if out.get("error"):
            raise HomeAssistantError(f"{self.name}: {out['error']}")
        return out

    async def esup_set(self, addrs: tuple[int, ...] | None, writes: dict[str, int]) -> dict:
        """Write ES-UP user params that differ, then verify in a fresh session."""
        async with self.lock, self._bms_ble_paused():
            before = await self._esup_session(self._esup_addrs(addrs), ("user_params",), writes)
            if before.get("error"):
                raise HomeAssistantError(f"{self.name}: {before['error']}")
            self.esup_addr = before["addr"]
            result: dict = {"addr": before["addr"], "auth": before.get("auth")}
            result["before"] = {k: before["user_params"].get(k) for k in writes}
            result["writes"] = before.get("writes", {})
            after_params = before["user_params"]
            if result["writes"]:
                await asyncio.sleep(2)
                after = await self._esup_session((before["addr"],), ("user_params",))
                after_params = after.get("user_params", {})
            result["after"] = {k: after_params.get(k) for k in writes}
            result["verified"] = all(result["after"].get(k) == v for k, v in writes.items())
            if result["writes"]:
                _LOGGER.warning("%s: ES-UP write %s", self.name, result)
        self._update(after_params)
        return result

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
