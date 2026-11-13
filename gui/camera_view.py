"""Video window for cameras: a live stream (RTSP from go2rtc, or HLS from the Mi cloud) or a recorded
event, played with Qt Multimedia (FFmpeg).

A camera can have several ways to deliver live video ("sources"). They are tried best-first; when one
fails before the first picture arrives, the next one is tried automatically. The address of a source
comes from a callable run off the UI thread, so it can be fetched again on every reconnect (go2rtc may
restart, Mi cloud addresses expire, battery cameras must be woken)."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from xc.cloud import CloudAuthError, friendly
from xc.device import NeedsSignIn

from .workers import run_task

FIRST_PICTURE_SECONDS = 40   # waking a battery camera + connecting, or the Mi cloud preparing HLS
# failures that trying again doesn't fix - the user has to do something first (install, sign in)
NEEDS_THE_USER = (FileNotFoundError, PermissionError, NeedsSignIn, CloudAuthError)


@dataclass
class Source:
    label: str                                   # shown in the window, e.g. "Mi cloud (WebRTC)"
    provider: Callable[[], str]                  # returns the address to play; may block; may raise
    note: str = ""                               # small print, e.g. "10-20 s behind"
    detail: Callable[[], str | None] | None = None   # better explanation after a failed start
    closed: Callable[[], None] | None = None     # the window stopped using this source


class CameraWindow(QWidget):
    def __init__(self, title: str, sources: list[Source], parent=None, live: bool = True):
        super().__init__(parent, Qt.Window)  # separate window, owned by the main window
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"{title} - {'live view' if live else 'recording'}")
        self.resize(960, 600)
        self.title = title
        self.sources = list(sources)
        self.live = live
        self.index = 0
        self._retries = 0            # reconnects of a source that already showed a picture
        self._had_picture = False    # this source showed a picture since it was (re)started
        self._attempt = 0            # guards against answers of an older connection attempt
        self._closing = False
        self._tried: set[int] = set()     # sources tried since the last picture (fallback round)
        self._touched: set[int] = set()   # sources ever started (their `closed` is called at the end)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
        except ImportError as exc:
            lay.addWidget(QLabel(f"Video playback is not available: {exc}"))
            return
        self.video = QVideoWidget()
        self.video.setStyleSheet("background:black;")
        lay.addWidget(self.video, 1)

        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 6, 10, 6)
        self.status = QLabel("Connecting…")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        bl.addWidget(self.status, 1)
        self.picker = QComboBox()
        self.picker.setToolTip("How the video reaches this PC")
        for s in self.sources:
            self.picker.addItem(s.label)
        self.picker.setVisible(len(self.sources) > 1)
        self.picker.activated.connect(self._picked)
        bl.addWidget(self.picker)
        self.mute_btn = QPushButton("🔇 Sound off")
        self.mute_btn.setCheckable(True)
        self.mute_btn.toggled.connect(self._mute)
        self.snap_btn = QPushButton("📷 Snapshot")
        self.snap_btn.clicked.connect(self.snapshot)
        full = QPushButton("⛶ Full screen")
        full.clicked.connect(self.toggle_full)
        again = QPushButton("⟳ Reconnect" if live else "⟳ Play again")
        again.clicked.connect(lambda: self.connect_stream(reset=True))
        for b in (self.mute_btn, self.snap_btn, full, again):
            b.setCursor(Qt.PointingHandCursor)
            bl.addWidget(b)
        lay.addWidget(bar)
        self.bar = bar

        self.audio = QAudioOutput()
        self.audio.setMuted(True)  # camera sound only when asked for
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.errorOccurred.connect(self._error)
        self.player.mediaStatusChanged.connect(self._status)
        self.video.videoSink().videoFrameChanged.connect(self._frame)
        self.QMediaPlayer = QMediaPlayer
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self.connect_stream)
        self._watchdog = QTimer(self)      # no picture at all within FIRST_PICTURE_SECONDS
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(lambda: self._failed("No picture arrived"))
        self.connect_stream()

    # -- connection ---------------------------------------------------------------------------
    @property
    def source(self) -> Source:
        return self.sources[self.index]

    def connect_stream(self, reset=False):
        if self._closing or not self.sources:
            return
        if reset:
            self._retries = 0
        self._attempt += 1
        attempt = self._attempt
        self._had_picture = False
        self._retry_timer.stop()
        self.player.stop()
        self._tried.add(self.index)
        self._touched.add(self.index)
        self.picker.setCurrentIndex(self.index)
        what = f"Connecting · {self.source.label}…" if self.live else "Loading the recording…"
        self.status.setText(what if not self._retries else f"Reconnecting (attempt {self._retries + 1})…")
        self._watchdog.start(FIRST_PICTURE_SECONDS * 1000)
        run_task(self.source.provider,
                 on_done=lambda url: attempt == self._attempt and self._play(url),
                 on_error=lambda exc: attempt == self._attempt and self._failed(friendly(exc), exc),
                 owner=self)

    def _play(self, url):
        if self._closing:
            return
        self.player.setSource(QUrl(url))
        self.player.play()

    def _picked(self, index):
        if index != self.index:
            self.index = index
            self._tried = set()
            self.connect_stream(reset=True)

    def _frame(self, frame):
        if self._had_picture or not frame.isValid():
            return
        self._had_picture = True
        self._retries = 0
        self._tried = {self.index}
        self._watchdog.stop()
        note = f" · {self.source.note}" if self.source.note else ""
        self.status.setText(("● Live" if self.live else "▶ Playing") + f" · {self.source.label}{note}")

    def _error(self, _err, msg):
        self._failed(msg or "The stream stopped")

    def _status(self, st):
        P = self.QMediaPlayer.MediaStatus
        if st == P.EndOfMedia and not self.live:
            self._watchdog.stop()
            self.status.setText("Finished")
        elif st in (P.EndOfMedia, P.InvalidMedia) or (st == P.StalledMedia and self._had_picture):
            self._failed("The stream stopped")

    def _failed(self, text: str, exc=None):
        """The current source failed: before its first picture -> try the next source; after it ->
        reconnect the same one."""
        if self._closing or self._retry_timer.isActive():
            return
        self._watchdog.stop()
        self.player.stop()
        detail = None
        if self.source.detail is not None:
            try:
                detail = self.source.detail()
            except Exception:  # noqa: BLE001
                detail = None
        text = detail or text
        if not self.live:
            self.status.setText(f"⚠ {text}")
            return
        if isinstance(exc, NEEDS_THE_USER) and len(self.sources) == 1:
            self.status.setText(f"⚠ {text}")
            return
        if not self._had_picture:
            nxt = next((i for i in range(len(self.sources)) if i not in self._tried), None)
            if nxt is not None:
                self.status.setText(f"⚠ {self.source.label}: {text}  ·  trying {self.sources[nxt].label}…")
                self.index = nxt
                self._retry_timer.start(1500)
                return
            # every way failed: start over with the best one, slower and slower
            self.index = 0
            self._tried = set()
        self._retries += 1
        delay = min(60, 5 * self._retries)
        self.status.setText(f"⚠ {text}  ·  retrying in {delay} s")
        self._retry_timer.start(delay * 1000)

    # -- controls -----------------------------------------------------------------------------
    def _mute(self, sound_on):
        self.audio.setMuted(not sound_on)
        self.mute_btn.setText("🔊 Sound on" if sound_on else "🔇 Sound off")

    def snapshot(self):
        frame = self.video.videoSink().videoFrame()
        img = frame.toImage() if frame.isValid() else None
        if img is None or img.isNull():
            self.status.setText("⚠ No picture yet")
            return
        folder = os.path.join(QStandardPaths.writableLocation(QStandardPaths.PicturesLocation), "XiControl")
        os.makedirs(folder, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in self.title).strip() or "camera"
        path = os.path.join(folder, f"{safe} {time.strftime('%Y-%m-%d %H-%M-%S')}.png")
        img.save(path)
        self.status.setText(f"Saved {path}")

    def toggle_full(self):
        if self.isFullScreen():
            self.showNormal()
            self.bar.show()
        else:
            self.bar.hide()
            self.showFullScreen()

    def mouseDoubleClickEvent(self, e):
        self.toggle_full()
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.isFullScreen():
            self.toggle_full()
            return
        if e.key() == Qt.Key_F11:
            self.toggle_full()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        self._closing = True
        if hasattr(self, "player"):
            self._retry_timer.stop()
            self._watchdog.stop()
            self.player.stop()
            for i in self._touched:
                cb = self.sources[i].closed
                if cb is not None:
                    try:
                        cb()
                    except Exception:  # noqa: BLE001
                        pass
        super().closeEvent(e)
