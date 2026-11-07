"""Write BMS_BLE / aiobmsble debug logs to daily files (gzipped at midnight) without flooding home-assistant.log."""
from __future__ import annotations

import datetime as dt
import gzip
import logging
from logging.handlers import QueueHandler, QueueListener, TimedRotatingFileHandler
import os
import queue
import shutil

LOGGERS = ("custom_components.bms_ble", "aiobmsble")
# Same layout as home-assistant.log, so bms_logs/bms_log_summary.py can parse the files.
FORMAT = "%(asctime)s.%(msecs)03d %(levelname)s (%(threadName)s) [%(name)s] %(message)s"
BASE = "bms_ble.log"
_LOGGER = logging.getLogger(__name__)


def _ours(name: str) -> bool:
    return any(name == n or name.startswith(n + ".") for n in LOGGERS)


class _DropOurDebug(logging.Filter):
    """Keeps our DEBUG records out of HA's own log handlers."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.INFO or not _ours(record.name)


class BmsDebugLog:
    def __init__(self) -> None:
        self.path: str | None = None
        self.keep_days = 0
        self._listener: QueueListener | None = None
        self._qh: QueueHandler | None = None
        self._levels: dict[str, int] = {}
        self._filter = _DropOurDebug()

    @property
    def running(self) -> bool:
        return self._listener is not None

    def _rotate(self, source: str, dest: str) -> None:
        with open(source, "rb") as fi, gzip.open(dest, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        os.remove(source)
        if self.keep_days:
            cutoff = dt.date.today() - dt.timedelta(days=self.keep_days)
            for f in os.listdir(self.path):
                try:
                    day = dt.date.fromisoformat(f[len("bms_ble_") : len("bms_ble_") + 10])
                except ValueError:
                    continue
                if f.startswith("bms_ble_") and day < cutoff:
                    os.remove(os.path.join(self.path, f))

    def start(self, path: str, keep_days: int) -> None:
        """Blocking (opens files): run in the executor."""
        self.stop()
        os.makedirs(path, exist_ok=True)
        self.path, self.keep_days = path, keep_days
        fh = TimedRotatingFileHandler(os.path.join(path, BASE), when="midnight", encoding="utf-8")
        # bms_ble.log.2026-11-07 -> bms_ble_2026-11-07.log.gz
        fh.namer = lambda name: os.path.join(path, f"bms_ble_{name.rsplit('.', 1)[-1]}.log.gz")
        fh.rotator = self._rotate
        fh.setFormatter(logging.Formatter(FORMAT, "%Y-%m-%d %H:%M:%S"))
        q: queue.SimpleQueue = queue.SimpleQueue()
        self._qh = QueueHandler(q)
        self._listener = QueueListener(q, fh)
        self._listener.start()
        for h in logging.getLogger().handlers:
            h.addFilter(self._filter)
        for name in LOGGERS:
            lg = logging.getLogger(name)
            self._levels[name] = lg.level
            lg.setLevel(logging.DEBUG)
            lg.addHandler(self._qh)
        _LOGGER.info("BMS debug log started: %s", os.path.join(path, BASE))

    def stop(self) -> None:
        if not self._listener:
            return
        for name in LOGGERS:
            lg = logging.getLogger(name)
            lg.removeHandler(self._qh)
            lg.setLevel(self._levels.get(name, logging.NOTSET))
        for h in logging.getLogger().handlers:
            h.removeFilter(self._filter)
        self._listener.stop()  # flushes the queue
        for h in self._listener.handlers:
            h.close()
        self._listener = self._qh = None
        _LOGGER.info("BMS debug log stopped")

    def status(self) -> dict:
        files: list[str] = []
        if self.path and os.path.isdir(self.path):
            files = sorted(f for f in os.listdir(self.path) if f.startswith("bms_ble"))
        return {"running": self.running, "path": self.path, "keep_days": self.keep_days, "files": files}
