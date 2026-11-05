"""Offline tests for custom_components/jbd_config/protocol.py with a fake BMS.

Run: python -m pytest tests -q
"""
import asyncio
import importlib.util
import pathlib

import pytest

_spec = importlib.util.spec_from_file_location(
    "protocol", pathlib.Path(__file__).parents[1] / "custom_components/jbd_config/protocol.py"
)
p = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(p)

MAC = "AA:C2:37:01:B9:02"


class FakeBms:
    """Answers auth and JBD frames; EEPROM changes only persist on exit-with-save."""

    def __init__(self, auth_status=b"\x00", password="123456"):
        self.eeprom = {0x12: 3600, 0x2A: 3400, 0x2B: 8}
        self.pending = None
        self.auth_status, self.password, self.rnd = auth_status, password, 0x42
        self.session = None
        self.buf = b""

    def _send(self, frame: bytes):
        for i in range(0, len(frame), 7):  # fragment replies
            self.session.on_notify(None, bytearray(frame[i : i + 7]))

    async def write(self, chunk: bytes):
        self.buf += chunk
        f = self.buf
        if f[:2] == b"\xff\xaa":
            if len(f) < 4 + f[3] + 1:
                return
            self.buf = b""
            cmd = f[2]
            if cmd == 0x15:
                d = self.auth_status
            elif cmd == 0x17:
                d = bytes([self.rnd])
            else:
                ok = f[4:10] == p.encrypt_password(MAC, self.password, self.rnd)
                d = b"\x00" if ok else b"\x01"
            self._send(p.auth_frame(cmd, d))
            return
        if len(f) < 4 + f[3] + 3:
            return
        self.buf = b""
        cmd, reg, val = f[1], f[2], int.from_bytes(f[4:6], "big")
        if cmd == 0x5A:
            if reg == 0x00:
                self.pending = dict(self.eeprom)
            elif reg == 0x01:
                if val == 0x2828:
                    self.eeprom = self.pending
                self.pending = None
            else:
                self.pending[reg] = val
            data = b""
        else:
            data = (self.pending or self.eeprom)[reg].to_bytes(2, "big")
        body = bytes([reg, 0, len(data)]) + data
        self._send(b"\xdd" + body + b"\x00\x00\x77")


def run(bms, changes, password="123456"):
    async def go():
        s = p.JbdSession(bms.write, MAC)
        bms.session = s
        await s.authenticate(password)
        return await s.factory(changes)

    return asyncio.run(go())


def test_factory_frame_matches_known_good():
    assert p.jbd_frame(0x5A, 0x00, bytes.fromhex("5678")).hex() == "dd5a00025678ff3077"


def test_read_does_not_save():
    bms = FakeBms()
    assert run(bms, {}) == {"cell_full_mv": 3600, "balance_start_mv": 3400, "balance_delta_mv": 8}
    assert bms.eeprom[0x12] == 3600


def test_write_saves_and_persists():
    bms = FakeBms()
    assert run(bms, {"cell_full_mv": 3500})["cell_full_mv"] == 3500
    assert run(bms, {})["cell_full_mv"] == 3500


def test_wrong_password_rejected():
    with pytest.raises(PermissionError):
        run(FakeBms(), {}, password="000000")


def test_no_password_module():
    assert run(FakeBms(auth_status=b"\x02"), {})["cell_full_mv"] == 3600
