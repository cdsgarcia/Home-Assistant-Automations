"""Offline test for custom_components/jbd_config/debuglog.py."""
import gzip
import importlib.util
import logging
import pathlib

_spec = importlib.util.spec_from_file_location(
    "debuglog", pathlib.Path(__file__).parents[1] / "custom_components/jbd_config/debuglog.py"
)
d = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(d)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_debug_goes_to_file_not_root_and_rotates_to_gz(tmp_path):
    root = logging.getLogger()
    cap = Capture()
    root.addHandler(cap)
    log = d.BmsDebugLog()
    try:
        log.start(str(tmp_path), keep_days=28)
        logging.getLogger("custom_components.bms_ble.coordinator").debug("M1: BMS data sample {'voltage': 53.1}")
        logging.getLogger("aiobmsble.bms.jbd_bms").warning("timeout")
        logging.getLogger("other").warning("unrelated")
        fh = log._listener.handlers[0]
        log._listener.stop()  # flush the queue
        log._listener.start()
        old = tmp_path / "bms_ble_2000-01-01.log.gz"
        old.write_bytes(b"")
        fh.doRollover()
    finally:
        log.stop()
        root.removeHandler(cap)

    assert not old.exists()  # older than keep_days
    gz = [f for f in tmp_path.iterdir() if f.name.endswith(".log.gz") and f.stat().st_size]
    text = "".join(gzip.open(f, "rt").read() for f in gz)
    assert "] M1: BMS data sample {'voltage': 53.1}" in text and "WARNING" in text
    assert "unrelated" not in text
    # HA's handlers still see warnings, but not our debug lines.
    assert [r.getMessage() for r in cap.records] == ["timeout", "unrelated"]
    assert logging.getLogger("aiobmsble").level == logging.NOTSET
