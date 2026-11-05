"""Read / set the JBD (Gentai) BMS "Cell Full Voltage" (EEPROM reg 0x12, cap_100, mV) over BLE.

The BMS recalibrates its SOC to 100% only when a cell reaches this voltage.
It is a per-cell threshold (any single cell), not a pack voltage: 3550 mV x 16 = 56.8 V.

Usage (needs `pip install bleak` and a Bluetooth adapter in range of the packs):
  python tools/jbd_cell_full_voltage.py                    # read all packs (no changes)
  python tools/jbd_cell_full_voltage.py --mv 3540          # dry run: show what would change
  python tools/jbd_cell_full_voltage.py --mv 3540 --write  # write, then verify by reading back
  python tools/jbd_cell_full_voltage.py --pack M1 --mv 3540 --write

Disable the pack's BMS_BLE integration entry in Home Assistant first - the BMS only accepts one
BLE connection at a time. Re-enable it afterwards.
"""
import argparse
import asyncio
import sys

PACKS = {
    "M1": "AA:C2:37:01:B9:02",
    "S1": "AA:C2:37:01:DB:D7",
    "S2": "AA:C2:37:01:E0:7F",
}
PASSWORD = "123456"
UUID_NOTIFY = "0000ff01-0000-1000-8000-00805f9b34fb"
UUID_WRITE = "0000ff02-0000-1000-8000-00805f9b34fb"

REG_FACTORY_ENTER = 0x00  # write 0x5678
REG_FACTORY_EXIT = 0x01  # write 0x2828 = save, 0x0000 = discard
REG_CELL_FULL = 0x12  # cap_100, mV
MV_MIN, MV_MAX = 3400, 3650  # sanity range for LFP


def jbd_frame(cmd: int, reg: int, data: bytes = b"") -> bytes:
    """DD <cmd> <reg> <len> <data> <crc16 BE> 77; crc = 0x10000 - sum(reg, len, data)."""
    body = bytes([reg, len(data)]) + data
    crc = (0x10000 - sum(body)) & 0xFFFF
    return bytes([0xDD, cmd]) + body + crc.to_bytes(2, "big") + b"\x77"


def auth_frame(cmd: int, data: bytes) -> bytes:
    """BLE-module auth frame: FF AA <cmd> <len> <data> <sum8(cmd, len, data)>."""
    body = bytes([cmd, len(data)]) + data
    return b"\xff\xaa" + body + bytes([sum(body) & 0xFF])


def encrypt_password(mac: str, password: str, rnd: int) -> bytes:
    mac_b = bytes.fromhex(mac.replace(":", ""))
    return bytes(((mac_b[i] ^ ord(password[i])) + rnd) & 0xFF for i in range(6))


class FrameAssembler:
    """Reassemble notification fragments into complete JBD (DD..77) or auth (FF AA..) frames."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        self.buf += chunk
        out = []
        while self.buf:
            if self.buf[0] == 0xDD:
                tail = 3  # crc16 + 0x77
            elif self.buf[:2] == b"\xff\xaa":
                tail = 1  # sum8
            elif len(self.buf) == 1 and self.buf[0] == 0xFF:
                break  # wait for the 2nd header byte
            else:
                del self.buf[0]  # resync
                continue
            if len(self.buf) < 4 or len(self.buf) < 4 + self.buf[3] + tail:
                break
            n = 4 + self.buf[3] + tail
            out.append(bytes(self.buf[:n]))
            del self.buf[:n]
        return out


class JbdSession:
    def __init__(self, client, mac: str) -> None:
        self.client, self.mac = client, mac
        self.asm = FrameAssembler()
        self.q: asyncio.Queue[bytes] = asyncio.Queue()

    def on_notify(self, _sender, data: bytearray) -> None:
        for f in self.asm.feed(bytes(data)):
            self.q.put_nowait(f)

    async def _xfer(self, frame: bytes, match, timeout: float = 3.0) -> bytes:
        while not self.q.empty():
            self.q.get_nowait()
        for i in range(0, len(frame), 20):
            await self.client.write_gatt_char(UUID_WRITE, frame[i : i + 20], response=False)
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while (left := end - loop.time()) > 0:
            try:
                f = await asyncio.wait_for(self.q.get(), left)
            except asyncio.TimeoutError:
                break
            if match(f):
                return f
        raise TimeoutError(f"no reply to {frame.hex(' ')}")

    async def _auth(self, cmd: int, data: bytes) -> bytes:
        f = await self._xfer(auth_frame(cmd, data), lambda f: f[:3] == bytes([0xFF, 0xAA, cmd]))
        return f[4 : 4 + f[3]]

    async def authenticate(self, password: str) -> None:
        try:
            st = await self._auth(0x15, b"000000")
        except TimeoutError:
            return  # module without auth
        if st[:1] == b"\x02":
            return  # no password set
        rnd = (await self._auth(0x17, b"\x00"))[0]
        if (await self._auth(0x18, encrypt_password(self.mac, password, rnd)))[:1] != b"\x00":
            raise RuntimeError("BLE password rejected")

    async def read_u16(self, reg: int) -> int:
        f = await self._xfer(jbd_frame(0xA5, reg), lambda f: f[0] == 0xDD and f[1] == reg)
        if f[2] != 0:
            raise RuntimeError(f"read reg 0x{reg:02X} failed (status 0x{f[2]:02X})")
        return int.from_bytes(f[4:6], "big")

    async def write_u16(self, reg: int, value: int) -> None:
        f = await self._xfer(
            jbd_frame(0x5A, reg, value.to_bytes(2, "big")), lambda f: f[0] == 0xDD and f[1] == reg
        )
        if f[2] != 0:
            raise RuntimeError(f"write reg 0x{reg:02X} failed (status 0x{f[2]:02X})")


async def run_factory(mac: str, new_mv: int | None = None) -> int:
    """Enter factory mode, optionally write REG_CELL_FULL, read it, exit (save only if written)."""
    from bleak import BleakClient  # lazy so the helpers can be tested without bleak

    async with BleakClient(mac, timeout=20) as client:
        s = JbdSession(client, mac)
        await client.start_notify(UUID_NOTIFY, s.on_notify)
        await s.authenticate(PASSWORD)
        await s.write_u16(REG_FACTORY_ENTER, 0x5678)
        save = False
        try:
            if new_mv is not None:
                await s.write_u16(REG_CELL_FULL, new_mv)
                save = True
            return await s.read_u16(REG_CELL_FULL)
        finally:
            await s.write_u16(REG_FACTORY_EXIT, 0x2828 if save else 0x0000)


async def process(name: str, mac: str, mv: int | None, write: bool) -> bool:
    cur = await run_factory(mac)
    print(f"{name} ({mac}): Cell Full Voltage = {cur} mV (x16 = {cur * 16 / 1000:.2f} V)")
    if mv is None:
        return True
    if cur == mv:
        print(f"{name}: already {mv} mV, nothing to do")
        return True
    if not write:
        print(f"{name}: would change {cur} -> {mv} mV (dry run; add --write)")
        return True
    await run_factory(mac, mv)
    await asyncio.sleep(2)
    new = await run_factory(mac)  # fresh session confirms it was saved to EEPROM
    ok = new == mv
    print(f"{name}: wrote {mv} mV, read back {new} mV -> {'OK' if ok else 'MISMATCH'}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", choices=sorted(PACKS), action="append", help="default: all packs")
    ap.add_argument("--mv", type=int, help=f"new Cell Full Voltage in mV ({MV_MIN}-{MV_MAX})")
    ap.add_argument("--write", action="store_true", help="actually write (otherwise dry run)")
    a = ap.parse_args()
    if a.mv is not None and not MV_MIN <= a.mv <= MV_MAX:
        ap.error(f"--mv must be {MV_MIN}-{MV_MAX}")
    ok = True
    for name in a.pack or sorted(PACKS):
        try:
            ok &= asyncio.run(process(name, PACKS[name], a.mv, a.write))
        except Exception as e:  # keep going with the other packs
            print(f"{name}: ERROR {e}")
            ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
