"""ES-UP address pinning in custom_components/jbd_config/pack.py (HA/bleak stubbed).

Every pack's BLE reaches the shared RS485 bus, so a fallback address reads/writes another pack.
"""
import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).parents[1] / "custom_components/jbd_config"


class HomeAssistantError(Exception):
    pass


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules.setdefault(name, mod)


for name, attrs in {
    "bleak": {}, "bleak.exc": {"BleakError": type("BleakError", (Exception,), {})},
    "bleak_retry_connector": {"BleakClientWithServiceCache": object, "establish_connection": None},
    "homeassistant": {}, "homeassistant.components": {"bluetooth": None},
    "homeassistant.config_entries": {"ConfigEntryState": None},
    "homeassistant.core": {"HomeAssistant": object, "callback": lambda f: f},
    "homeassistant.exceptions": {"HomeAssistantError": HomeAssistantError},
}.items():
    _stub(name, **attrs)

pkg = types.ModuleType("jbdc")
pkg.__path__ = [str(ROOT)]
sys.modules["jbdc"] = pkg
for mod in ("protocol", "pack"):
    spec = importlib.util.spec_from_file_location(f"jbdc.{mod}", ROOT / f"{mod}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[f"jbdc.{mod}"] = m
    spec.loader.exec_module(m)
JbdPack = sys.modules["jbdc.pack"].JbdPack
Err = sys.modules["jbdc.pack"].HomeAssistantError


def test_pinned_pack_never_falls_back():
    p = JbdPack(None, "M1", "AA:C2:37:01:B9:02", "123456", esup_address=1)
    assert p._esup_addrs(None) == (1,)
    assert p._esup_addrs(None, write=True) == (1,)
    assert p._esup_addrs((1,), write=True) == (1,)
    assert p._esup_addrs((2,)) == (2,)  # explicit diagnostic read is allowed
    with pytest.raises(Err):
        p._esup_addrs((2,), write=True)
    with pytest.raises(Err):
        p._esup_addrs((1, 2), write=True)


def test_unpinned_pack_writes_need_one_explicit_address():
    p = JbdPack(None, "M1", "AA:C2:37:01:B9:02", "123456")
    assert p._esup_addrs(None)[0] == 1
    with pytest.raises(Err):
        p._esup_addrs(None, write=True)
    with pytest.raises(Err):
        p._esup_addrs((1, 2), write=True)
    assert p._esup_addrs((3,), write=True) == (3,)
