"""JBD BMS BLE protocol: frames, BLE-module auth, factory-mode register access.

Transport-agnostic: JbdSession takes an async `write(bytes)` callable, and the caller feeds
notifications into `on_notify`. This keeps it testable without bleak or Home Assistant.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

UUID_NOTIFY = "0000ff01-0000-1000-8000-00805f9b34fb"
UUID_WRITE = "0000ff02-0000-1000-8000-00805f9b34fb"

REG_FACTORY_ENTER = 0x00
FACTORY_KEY = 0x5678
REG_FACTORY_EXIT = 0x01
EXIT_SAVE = 0x2828
EXIT_DISCARD = 0x0000
ROOT_PASSWORD = "JBDbtpwd!@#2023"  # fixed JBD BLE-module root password (syssi/esphome-jbd-bms)

# Writable EEPROM settings: name -> (register, min, max). All U16, mV.
SETTINGS: dict[str, tuple[int, int, int]] = {
    "cell_full_mv": (0x12, 3400, 3650),  # SOC resets to 100% when a cell reaches this
    "balance_start_mv": (0x2A, 3300, 3600),
    "balance_delta_mv": (0x2B, 5, 50),
}


# ES-UP ("polyglot" UP16S firmware) second protocol: Modbus-like, fn 0x78 read / 0x79 write.
# See alexbadcat/jbd-bms-toolkit docs/protocol-es-up.md. Writes are limited to ESUP_WRITABLE.
ESUP_READ, ESUP_WRITE = 0x78, 0x79
ESUP_MARKER = b"\x11JBD"  # mandatory before a write payload
ESUP_WRITABLE: dict[str, int] = {"full_adjust_v_x100": 0x1C0C, "full_adjust_c_x100": 0x1C0E}
ESUP_BLOCKS: dict[str, tuple[int, int]] = {
    "user_params": (0x1C00, 0x1C88),
    "capacity": (0x2000, 0x2040),
    "factory_mirror": (0x3900, 0x3940),
}


def crc16_modbus(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc.to_bytes(2, "little")


def esup_read_frame(addr: int, start: int, end: int) -> bytes:
    body = bytes([addr, ESUP_READ]) + start.to_bytes(2, "big") + end.to_bytes(2, "big") + b"\x00\x00"
    return body + crc16_modbus(body)


def esup_write_frame(addr: int, reg: int, value: int) -> bytes:
    payload = ESUP_MARKER + value.to_bytes(2, "big")
    body = bytes([addr, ESUP_WRITE]) + reg.to_bytes(2, "big") + (reg + 2).to_bytes(2, "big")
    body += len(payload).to_bytes(2, "big") + payload
    return body + crc16_modbus(body)


def _u16(d: bytes, off: int) -> int | None:
    return int.from_bytes(d[off : off + 2], "big") if len(d) >= off + 2 else None


def esup_decode(block: str, d: bytes) -> dict:
    """Decode known fields (raw units noted in key names)."""
    if block == "user_params":
        code = d[16:48].split(b"\x00")[0].decode("ascii", "replace") if len(d) >= 48 else None
        return {
            "balance_start_mv": _u16(d, 4),
            "balance_diff_mv": _u16(d, 6),
            "full_adjust_v_x100": _u16(d, 12),
            "full_adjust_c_x100": _u16(d, 14),
            "bms_code": code,
            "sleep_mv": _u16(d, 120),
            "sleep_delay": _u16(d, 122),
            "balance_mode": _u16(d, 124),
            "rs485_protocol": _u16(d, 126),
            "can_protocol": _u16(d, 128),
        }
    if block == "capacity":
        return {
            "nominal_ah_x100": _u16(d, 0),
            "full_ah_x100": _u16(d, 2),
            "remaining_ah_x100": _u16(d, 4),
            "soc_x100": _u16(d, 6),
            "cycles": _u16(d, 10),
        }
    return {}


def jbd_frame(cmd: int, reg: int, data: bytes = b"") -> bytes:
    """DD <cmd A5 read / 5A write> <reg> <len> <data> <crc16 BE> 77; crc = 0x10000 - sum(reg, len, data)."""
    body = bytes([reg, len(data)]) + data
    crc = (0x10000 - sum(body)) & 0xFFFF
    return bytes([0xDD, cmd]) + body + crc.to_bytes(2, "big") + b"\x77"


def auth_frame(cmd: int, data: bytes) -> bytes:
    """FF AA <cmd> <len> <data> <sum8(cmd, len, data)>."""
    body = bytes([cmd, len(data)]) + data
    return b"\xff\xaa" + body + bytes([sum(body) & 0xFF])


def encrypt_password(mac: str, password: str, rnd: int) -> bytes:
    """((mac[i] ^ pw[i]) + rnd) & 0xFF; MAC bytes beyond the 6th count as 0."""
    mac_b = bytes.fromhex(mac.replace(":", ""))
    return bytes((((mac_b[i] if i < 6 else 0) ^ ord(c)) + rnd) & 0xFF for i, c in enumerate(password))


class FrameAssembler:
    """Reassemble notification fragments into JBD (DD..77) or auth (FF AA..) frames."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        self.buf += chunk
        out: list[bytes] = []
        while self.buf:
            if self.buf[0] == 0xDD:
                tail = 3
            elif self.buf[:2] == b"\xff\xaa":
                tail = 1
            elif len(self.buf) == 1 and (self.buf[0] == 0xFF or self.buf[0] < 0x20):
                break
            elif self.buf[0] < 0x20 and self.buf[1] in (ESUP_READ, ESUP_WRITE):
                # ES-UP: addr 78 start:2 end:2 dlen:2 data crc:2
                if len(self.buf) < 8 or len(self.buf) < 10 + int.from_bytes(self.buf[6:8], "big"):
                    break
                n = 10 + int.from_bytes(self.buf[6:8], "big")
                out.append(bytes(self.buf[:n]))
                del self.buf[:n]
                continue
            else:
                del self.buf[0]
                continue
            if len(self.buf) < 4 or len(self.buf) < 4 + self.buf[3] + tail:
                break
            n = 4 + self.buf[3] + tail
            out.append(bytes(self.buf[:n]))
            del self.buf[:n]
        return out


class JbdSession:
    def __init__(self, write: Callable[[bytes], Awaitable[None]], mac: str) -> None:
        self._write, self.mac = write, mac
        self._asm = FrameAssembler()
        self._q: asyncio.Queue[bytes] = asyncio.Queue()
        self._rx = bytearray()

    def on_notify(self, _sender, data: bytearray) -> None:
        self._rx += data
        for f in self._asm.feed(bytes(data)):
            self._q.put_nowait(f)

    async def _xfer(self, frame: bytes, match: Callable[[bytes], bool], timeout: float = 3.0) -> bytes:
        while not self._q.empty():
            self._q.get_nowait()
        for i in range(0, len(frame), 20):
            await self._write(frame[i : i + 20])
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while (left := end - loop.time()) > 0:
            try:
                f = await asyncio.wait_for(self._q.get(), left)
            except asyncio.TimeoutError:
                break
            if match(f):
                return f
        raise TimeoutError(f"no reply to {frame.hex(' ')}")

    async def _auth(self, cmd: int, data: bytes) -> bytes:
        f = await self._xfer(auth_frame(cmd, data), lambda f: f[:3] == bytes([0xFF, 0xAA, cmd]))
        return f[4 : 4 + f[3]]

    async def authenticate(self, password: str) -> str:
        """BLE-module auth: user password, then root password. Returns the auth stage reached."""
        try:
            st = await self._auth(0x15, b"000000")
        except TimeoutError:
            return "no_auth_module"
        if st[:1] == b"\x02":
            return f"no_password_set ({st.hex()})"
        rnd = (await self._auth(0x17, b""))[0]
        if (await self._auth(0x18, encrypt_password(self.mac, password, rnd)))[:1] != b"\x00":
            raise PermissionError("BLE password rejected")
        # Root level (as used by the JBD app) may be required to write protected EEPROM registers.
        # The vendor app reuses the user-password random byte here (syssi/esphome-jbd-bms#94 capture).
        try:
            r = await self._auth(0x1D, encrypt_password(self.mac, ROOT_PASSWORD, rnd))
        except TimeoutError:
            return f"user_ok, root_timeout (0x15={st.hex()})"
        return "root_ok" if r[:1] == b"\x00" else f"user_ok, root_rejected ({r.hex()})"

    async def read_u16(self, reg: int) -> int:
        f = await self._xfer(jbd_frame(0xA5, reg), lambda f: f[0] == 0xDD and f[1] == reg)
        if f[2] != 0:
            raise OSError(f"read reg 0x{reg:02X} failed (status 0x{f[2]:02X})")
        return int.from_bytes(f[4:6], "big")

    async def write_u16(self, reg: int, value: int) -> None:
        f = await self._xfer(
            jbd_frame(0x5A, reg, value.to_bytes(2, "big")), lambda f: f[0] == 0xDD and f[1] == reg
        )
        if f[2] != 0:
            raise OSError(f"write reg 0x{reg:02X} failed (status 0x{f[2]:02X})")

    async def esup_read(self, addr: int, start: int, end: int, timeout: float = 3.0) -> bytes:
        f = await self._xfer(
            esup_read_frame(addr, start, end),
            lambda f: len(f) >= 10 and f[1] == ESUP_READ and f[2:4] == start.to_bytes(2, "big"),
            timeout,
        )
        if crc16_modbus(f[:-2]) != f[-2:]:
            raise OSError(f"ES-UP CRC mismatch: {f.hex(' ')}")
        return f[8:-2]

    async def esup_write(self, addr: int, reg: int, value: int, timeout: float = 1.5) -> str:
        """Write one u16. The ack format is undocumented, so return the raw bytes received; verify by re-reading."""
        self._rx.clear()
        try:
            await self._xfer(esup_write_frame(addr, reg, value), lambda f: f[1] == ESUP_WRITE, timeout)
        except TimeoutError:
            pass
        self._asm.buf.clear()  # drop any partial, unparsed ack
        return self._rx.hex(" ")

    async def factory(self, changes: dict[str, int], raw_regs: tuple[int, ...] = ()) -> dict:
        """Enter factory mode, apply `changes`, read all SETTINGS (+ raw_regs), exit (save only if changed)."""
        await self.write_u16(REG_FACTORY_ENTER, FACTORY_KEY)
        save = False
        try:
            for name, value in changes.items():
                await self.write_u16(SETTINGS[name][0], value)
                save = True
            out: dict = {name: await self.read_u16(reg) for name, (reg, _, _) in SETTINGS.items()}
            if raw_regs:
                out["raw"] = {f"0x{r:02X}": await self.read_u16(r) for r in raw_regs}
            return out
        finally:
            await self.write_u16(REG_FACTORY_EXIT, EXIT_SAVE if save else EXIT_DISCARD)
