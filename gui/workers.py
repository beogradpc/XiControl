"""Run blocking network calls off the UI thread."""
from __future__ import annotations

import logging
import traceback

import requests
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from xc.camera import CameraError
from xc.cloud import CloudError
from xc.device import ControlError
from xc.miio import MiioError

log = logging.getLogger(__name__)
# failures that are part of normal life (device unplugged, no internet) - logged briefly
EXPECTED = (CloudError, ControlError, MiioError, CameraError, requests.RequestException, OSError)

try:
    from shiboken6 import isValid
except ImportError:  # pragma: no cover
    def isValid(_obj):
        return True

_alive: set = set()
_pool = QThreadPool.globalInstance()
_pool.setMaxThreadCount(8)


class _Relay(QObject):
    """Lives on the UI thread; worker threads emit, Qt queues the slot call."""
    done = Signal(object)
    failed = Signal(object)

    def __init__(self, on_done, on_error, owner):
        super().__init__()
        self.on_done, self.on_error, self.owner = on_done, on_error, owner
        self.done.connect(self._done)
        self.failed.connect(self._failed)

    def _call(self, cb, value):
        _alive.discard(self)
        if cb is None or (self.owner is not None and not isValid(self.owner)):
            return
        cb(value)

    @Slot(object)
    def _done(self, value):
        self._call(self.on_done, value)

    @Slot(object)
    def _failed(self, exc):
        self._call(self.on_error, exc)


class _Task(QRunnable):
    def __init__(self, fn, args, kwargs, relay):
        super().__init__()
        self.fn, self.args, self.kwargs, self.relay = fn, args, kwargs, relay

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001
            exc.trace = traceback.format_exc()
            name = getattr(self.fn, "__qualname__", repr(self.fn))
            if isinstance(exc, EXPECTED):
                log.debug("%s failed: %s", name, exc)
            else:
                log.error("%s failed unexpectedly:\n%s", name, exc.trace)
            signal, value = self.relay.failed, exc
        else:
            signal, value = self.relay.done, result
        try:
            signal.emit(value)
        except RuntimeError:
            pass  # the app is shutting down


def run_task(fn, *args, on_done=None, on_error=None, owner=None, **kwargs):
    """Run fn(*args) in the thread pool; callbacks run on the UI thread.
    If `owner` (a QWidget) has been destroyed by then, the callbacks are skipped."""
    relay = _Relay(on_done, on_error, owner)
    _alive.add(relay)
    task = _Task(fn, args, kwargs, relay)
    task.setAutoDelete(True)
    _pool.start(task)
    return relay
