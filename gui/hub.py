"""Keeps the live state of every device (for the home-screen tiles) and refreshes it in the background."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from xc.catalog import fill, legacy_profile
from xc.cloud import CloudAuthError, friendly
from xc.device import DeviceController, DeviceOffline, NeedsSignIn
from xc.summary import LegacySummary, Summary

from .workers import run_task


OFFLINE_TEXT = "Offline (reported by the Mi cloud)"


def backoff(fails: int) -> float:
    """Seconds to wait before polling a device again after `fails` failures in a row."""
    return 0.0 if fails < 3 else min(300.0, 30.0 * 2 ** (fails - 3))


class DeviceState:
    def __init__(self):
        self.values: dict = {}          # (siid, piid) -> value, or legacy key -> value
        self.online = None              # True / False / None (unknown)
        self.summary = None             # Summary | LegacySummary | None
        self.spec = None
        self.spec_state = "none"        # none | loading | ready | missing | failed
        self.pending = False
        self.error = ""
        self.transport = ""
        self.needs_cloud = False
        self.setup_needed = False       # IP address / token missing or wrong for a LAN-only device
        self.fails = 0                  # failed polls in a row
        self.next_poll = 0.0            # monotonic time before which background polls are skipped
        self.spec_fails = 0
        self.spec_retry_at = 0.0


class Hub(QObject):
    changed = Signal(str)               # did
    spec_ready = Signal(str)            # did
    auth_expired = Signal()

    def __init__(self, cfg, specs, controller_for, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.specs = specs
        self.controller_for = controller_for
        self.states: dict[str, DeviceState] = {}
        self.active_did = None          # the device page polls this one itself
        self.cloud_online: dict[str, bool] = {}   # the Mi account's own view: {did: online}
        self._online_at = 0.0                     # when that view was fetched (monotonic)
        self._batch_pending = False
        self._batch_again = False
        self._soon = QTimer(self)
        self._soon.setSingleShot(True)
        self._soon.setInterval(400)
        self._soon.timeout.connect(self.poll_all)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_all)
        self.set_interval(cfg.data.get("refresh_seconds", 10))

    def set_interval(self, seconds):
        self.timer.setInterval(max(5, int(seconds)) * 1000)

    def start(self):
        self.timer.start()
        self.poll_all()

    def listed_offline(self, did) -> bool:
        """Does the Mi account list this device as offline? (Its last answer, unless that is old.)"""
        fresh = time.monotonic() - self._online_at < 2 * self.timer.interval() / 1000 + 60
        return fresh and self.cloud_online.get(str(did)) is False

    def state(self, did) -> DeviceState:
        did = str(did)
        if did not in self.states:
            self.states[did] = DeviceState()
        return self.states[did]

    def forget(self, did):
        self.states.pop(str(did), None)

    # -- spec / summary -------------------------------------------------------------------
    def ensure_summary(self, did):
        st = self.state(did)
        info = self.cfg.device(did)
        if not info or st.spec_state in ("loading", "ready", "missing"):
            return
        profile = legacy_profile(info.get("model", ""))
        st.spec_state = "loading"

        def done(spec):
            st.spec = spec
            st.spec_fails = 0
            # Older devices (Yeelight, Roborock, plugs) get their legacy panel when they are controlled over
            # the LAN, where they only understand the old commands. Through the Mi cloud the standard
            # description works for them too - and lets them be polled together with all other devices.
            ctl = self.controller_for(info)
            legacy = profile and (spec is None or (ctl.local_possible() and ctl.hub() is None))
            if legacy:
                st.summary = LegacySummary(profile)
            elif spec is not None:
                st.summary = Summary(spec)
            st.spec_state = "ready" if (spec is not None or profile) else "missing"
            self.spec_ready.emit(str(did))
            first_poll()

        def failed(exc):
            st.spec_state = "failed"
            st.spec_fails += 1
            st.spec_retry_at = time.monotonic() + min(600, 30 * 2 ** (st.spec_fails - 1))
            st.error = friendly(exc)
            if profile:
                st.summary = LegacySummary(profile)
            self.spec_ready.emit(str(did))
            self.changed.emit(str(did))
            first_poll()

        def first_poll():
            # cloud devices wait a moment and are then read all together, not one request each
            if self.controller_for(info).prefers_cloud() and not isinstance(st.summary, LegacySummary) \
                    and str(did) != self.active_did:
                self.poll_soon()
            else:
                self.poll(did, force=True)

        run_task(self.specs.get, info.get("model", ""), on_done=done, on_error=failed)

    def reload_spec(self, did):
        info = self.cfg.device(did)
        if info:
            self.specs.forget(info.get("model", ""))
        st = self.state(did)
        st.spec_state = "none"
        st.summary = None
        st.spec = None
        st.spec_retry_at = 0.0
        self.ensure_summary(did)

    # -- polling ----------------------------------------------------------------------------
    def refresh_now(self):
        """The user asked for fresh values: everything is read now, also the devices that were waiting."""
        for st in self.states.values():
            st.next_poll = 0.0
            if st.spec_state == "failed":
                st.spec_retry_at = 0.0
        self.poll_all()

    def poll_soon(self):
        """A poll of everything shortly - called many times in a row, it still runs once."""
        self._soon.start()

    def poll_all(self):
        now = time.monotonic()
        # Devices that are reached through the Mi cloud (the only way for them, or their LAN address
        # didn't answer) are handled together: one request tells which of them are online, a second one
        # reads all their values.
        batch = []
        cloud = None
        for d in list(self.cfg.devices):
            did = str(d["did"])
            if did == self.active_did:
                continue
            st = self.state(did)
            if st.spec_state in ("none", "failed") and st.summary is None and now >= st.spec_retry_at:
                self.ensure_summary(did)
                continue
            ctl = self.controller_for(d)
            if ctl.prefers_cloud() and not isinstance(st.summary, LegacySummary):
                if not st.pending:
                    batch.append((did, st.summary.tile_pairs() if isinstance(st.summary, Summary) else []))
                    cloud = ctl.cloud
                continue
            self.poll(did)
        if batch:
            if self._batch_pending:
                self._batch_again = True
            else:
                self._poll_cloud_batch(cloud, batch)

    def _poll_cloud_batch(self, cloud, batch):
        self._batch_pending = True
        known = set()  # devices whose remembered values we already have
        for did, _pairs in batch:
            st = self.state(did)
            st.pending = True
            st.needs_cloud = st.setup_needed = False
            if st.values:
                known.add(did)

        def work():
            # The cloud answers a read with remembered values even for a device that is switched off, so
            # "it answered" doesn't mean "it is online" - the account's device list says that.
            online = cloud.online_states()
            # an offline device is read once (for its last values) and then left alone until it is back
            params = [{"did": did, "siid": s, "piid": p} for did, pairs in batch
                      if not (online.get(did) is False and did in known) for s, p in pairs]
            return online, (cloud.miot_get_many(params) if params else [])

        def done(result):
            online, results = result
            self._batch_pending = False
            self.cloud_online, self._online_at = online, time.monotonic()
            by_did: dict[str, list] = {}
            for item in results or []:
                if isinstance(item, dict):
                    by_did.setdefault(str(item.get("did")), []).append(item)
            for did, _pairs in batch:
                st = self.state(did)
                st.pending = False
                flag, items = online.get(did), by_did.get(did)
                if flag is None and not items:
                    continue  # the cloud said nothing about this device: keep what we know
                was = (st.online, st.error)
                offline, values = flag is False, {}
                if items:
                    try:
                        values = DeviceController.parse_props(items)
                    except DeviceOffline:
                        offline = True
                changed = any(st.values.get(k) != v for k, v in values.items())
                st.values.update(values)
                if offline:
                    st.online, st.error = False, OFFLINE_TEXT
                    st.fails += 1
                else:
                    st.online, st.error, st.fails, st.next_poll = True, "", 0, 0.0
                    st.transport = "cloud"
                # repeat the signal for a freshly offline device: the tray waits for two misses in a row
                if changed or was != (st.online, st.error) or (offline and st.fails <= 3):
                    self.changed.emit(did)
            if self.active_did:  # the open page reads its own values; whether it is online comes from here
                st = self.state(self.active_did)
                if st.transport == "cloud" and st.online and online.get(self.active_did) is False:
                    st.online, st.error = False, OFFLINE_TEXT
                    self.changed.emit(self.active_did)
            if self._batch_again:
                self._batch_again = False
                self.poll_soon()

        def failed(exc):
            self._batch_pending = self._batch_again = False
            if isinstance(exc, CloudAuthError):
                self.auth_expired.emit()
            for did, _pairs in batch:
                st = self.state(did)
                st.pending = False
                st.online, st.error = False, friendly(exc)
                st.fails += 1
                self.changed.emit(did)

        run_task(work, on_done=done, on_error=failed)

    def poll(self, did, pairs=None, force=False):
        did = str(did)
        st = self.state(did)
        info = self.cfg.device(did)
        if not info or st.pending or (not force and time.monotonic() < st.next_poll):
            return
        ctl = self.controller_for(info)
        reason = ctl.unreachable_reason()
        if reason:
            st.online = None
            st.needs_cloud = ctl.needs_sign_in()
            st.setup_needed = not st.needs_cloud
            st.error = reason
            self.changed.emit(did)
            return
        st.needs_cloud = st.setup_needed = False
        summ = st.summary
        if isinstance(summ, LegacySummary):
            s = summ.profile["status"]
            fn, args = ctl.raw, (s["method"], s["params"])
            convert = summ.parse
        elif isinstance(summ, Summary) and (pairs or summ.tile_pairs()):
            pairs = pairs or summ.tile_pairs()
            fn, args = ctl.get_props, (pairs,)
            convert = None
        elif ctl.local_possible() and not ctl.prefers_cloud():
            # nothing to read (e.g. a router): at least see whether it answers on the network
            fn, args, convert = ctl.info_local, (), (lambda _r: {})
        else:
            return
        presence_only = fn == ctl.info_local
        st.pending = True

        def done(result):
            st.pending = False
            st.values.update(convert(result) if convert else result)
            st.transport = "local" if presence_only else ctl.last_transport
            # The cloud answers a read with remembered values even for a device that is switched off;
            # whether it is online is what the account's device list says.
            # The same goes for a Zigbee device its gateway has just lost - unless the account says it is online.
            lost = getattr(ctl, "lost_by_hub", False) and self.cloud_online.get(did) is not True
            if fn == ctl.get_props and st.transport == "cloud" and (self.listed_offline(did) or lost):
                st.online, st.error = False, (OFFLINE_TEXT if self.listed_offline(did) else
                                              "Offline (its hub has no contact with it)")
                st.fails += 1
                st.next_poll = time.monotonic() + backoff(st.fails)
            else:
                st.online, st.error, st.fails, st.next_poll = True, "", 0, 0.0
            self.changed.emit(did)

        def failed(exc):
            st.pending = False
            if presence_only and ctl.prefers_cloud():
                self.poll_soon()  # not on this network after all: the Mi account knows whether it is online
                return
            if isinstance(exc, NeedsSignIn):
                st.online, st.needs_cloud = None, True
            else:
                st.online = False
            offline = isinstance(exc, DeviceOffline) or self.listed_offline(did)
            st.error = OFFLINE_TEXT if offline else friendly(exc)
            st.fails += 1
            st.next_poll = time.monotonic() + backoff(st.fails)
            if isinstance(exc, CloudAuthError):
                self.auth_expired.emit()
            self.changed.emit(did)

        run_task(fn, *args, on_done=done, on_error=failed)

    def push(self, did, values: dict, online=True, transport=""):
        """Fresh values read by the device page."""
        st = self.state(did)
        st.values.update(values)
        st.online = online
        if transport:
            st.transport = transport
        if online:
            st.error = ""
            st.fails = 0
            st.next_poll = 0.0
        self.changed.emit(str(did))

    # -- commands -----------------------------------------------------------------------------
    def is_on(self, did):
        st = self.state(did)
        return st.summary.is_on(st.values) if st.summary else None

    def has_power(self, did) -> bool:
        st = self.state(did)
        if isinstance(st.summary, Summary):
            return st.summary.power is not None
        if isinstance(st.summary, LegacySummary):
            return bool(st.summary.profile.get("power"))
        return False

    def set_power(self, did, on: bool, done=None, error=None):
        did = str(did)
        st = self.state(did)
        info = self.cfg.device(did)
        if not info or not st.summary:
            return
        ctl = self.controller_for(info)
        summ = st.summary
        if isinstance(summ, Summary) and summ.power:
            p = summ.power
            st.values[(p.siid, p.piid)] = on  # optimistic
            fn, args = ctl.set_prop, (p.siid, p.piid, on)
        elif isinstance(summ, LegacySummary) and summ.profile.get("power"):
            pw = summ.profile["power"]
            method, params = pw["on" if on else "off"]
            st.values[pw["key"]] = pw["on_value"] if on else "off"
            fn, args = ctl.raw, (method, fill(params, None))
        else:
            return
        self.changed.emit(did)

        def ok(_r):
            st.online = True
            st.transport = ctl.last_transport
            self.changed.emit(did)
            QTimer.singleShot(800, lambda: self.poll(did, force=True))
            if done:
                done()

        def bad(exc):
            if isinstance(exc, CloudAuthError):
                self.auth_expired.emit()
            self.poll(did, force=True)
            if error:
                error(exc)

        run_task(fn, *args, on_done=ok, on_error=bad)
