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
        self.root = False
        self.buf = b""
        self.esup_addr = 1
        self.esup_ack = True
        self.esup_params = bytearray(136)
        self.esup_params[12:14], self.esup_params[14:16] = (5760).to_bytes(2, "big"), (1500).to_bytes(2, "big")
        self.esup_params[16:31] = b"UP16S019-XMDZ03"

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
                self.rnd = (self.rnd + 0x11) & 0xFF  # fresh random per request
                d = bytes([self.rnd])
            elif cmd == 0x18:
                ok = f[4:10] == p.encrypt_password(MAC, self.password, self.rnd)
                self.login_rnd = self.rnd
                d = b"\x00" if ok else b"\x01"
            else:  # 0x1D root password, encrypted with the login random byte
                self.root = f[4 : 4 + f[3]] == p.encrypt_password(MAC, p.ROOT_PASSWORD, self.login_rnd)
                d = b"\x00" if self.root else b"\x01"
            self._send(p.auth_frame(cmd, d))
            return
        if f[:1] != b"\xdd" and len(f) >= 2 and f[1] == p.ESUP_READ:
            if len(f) < 10:
                return
            self.buf = b""
            if f[0] != self.esup_addr:
                return  # wrong address: no reply
            data = self.esup_params
            hdr = f[:6] + len(data).to_bytes(2, "big") + data
            self._send(hdr + p.crc16_modbus(hdr))
            return
        if f[:1] != b"\xdd" and len(f) >= 8 and f[1] == p.ESUP_WRITE:
            if len(f) < 10 + int.from_bytes(f[6:8], "big"):
                return
            self.buf = b""
            assert f[8:12] == p.ESUP_MARKER and p.crc16_modbus(f[:-2]) == f[-2:]
            off = int.from_bytes(f[2:4], "big") - 0x1C00
            self.esup_params[off : off + 2] = f[12:14]
            if self.esup_ack:
                ack = f[:6] + b"\x00\x00"
                self._send(ack + p.crc16_modbus(ack))
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
        bms.auth = await s.authenticate(password)
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


def test_root_password_sent_after_user_password():
    bms = FakeBms()
    run(bms, {})
    assert bms.root and bms.auth == "root_ok"

def test_encryption_matches_vendor_app_capture():
    # syssi/esphome-jbd-bms#94: user "123123" then root, both with random 0x37
    mac = "A4:C1:37:04:2D:BE"
    assert p.encrypt_password(mac, "123123", 0x37).hex() == "cc2a3b6c56c4"
    assert p.encrypt_password(mac, p.ROOT_PASSWORD, 0x37).hex() == "25baaa9d9005ae9b58775a6967696a"


def esup(bms, addr, timeout=3.0):
    async def go():
        bms.session = p.JbdSession(bms.write, MAC)
        return await bms.session.esup_read(addr, *p.ESUP_BLOCKS["user_params"], timeout=timeout)

    return asyncio.run(go())


def test_esup_frame_matches_known_good():
    # alexbadcat/jbd-bms-toolkit protocol-es-up.md example
    assert p.esup_read_frame(1, 0x1C00, 0x1CA0).hex() == "01781c001ca000007c2e"


def test_esup_read_reassembles_and_decodes():
    d = p.esup_decode("user_params", esup(FakeBms(), 1))
    assert d["full_adjust_v_x100"] == 5760 and d["full_adjust_c_x100"] == 1500
    assert d["bms_code"] == "UP16S019-XMDZ03"


def test_esup_wrong_address_times_out():
    with pytest.raises((asyncio.TimeoutError, TimeoutError, OSError)):
        esup(FakeBms(), 2, timeout=0.2)


def test_esup_crc_mismatch_rejected():
    bms = FakeBms()
    send = bms._send
    bms._send = lambda fr: send(fr[:-1] + bytes([fr[-1] ^ 0xFF]))
    with pytest.raises(OSError, match="CRC"):
        esup(bms, 1)


def test_esup_write_frame_matches_known_good():
    # FullAdjustV 56.30 V example from alexbadcat/jbd-bms-toolkit
    assert p.esup_write_frame(1, 0x1C0C, 5630).hex() == "01791c0c1c0e0006114a424415fe4dbe"


@pytest.mark.parametrize("ack", [True, False])
def test_esup_write_persists_with_or_without_ack(ack):
    bms = FakeBms()
    bms.esup_ack = ack

    async def go():
        bms.session = p.JbdSession(bms.write, MAC)
        for k, v in (("full_adjust_v_x100", 5600), ("full_adjust_c_x100", 200)):
            await bms.session.esup_write(1, p.ESUP_WRITABLE[k], v, timeout=0.2)

    asyncio.run(go())
    d = p.esup_decode("user_params", esup(bms, 1))
    assert (d["full_adjust_v_x100"], d["full_adjust_c_x100"], d["bms_code"]) == (5600, 200, "UP16S019-XMDZ03")
