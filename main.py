"""XiControl - control Xiaomi devices from your PC over the home network."""
import logging
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

log = logging.getLogger("xicontrol")


def _fatal(text: str):
    """Show a start-up error even when Qt itself failed to load (pythonw has no console)."""
    print(text, file=sys.stderr)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "XiControl could not start", 0x10)
        except Exception:  # noqa: BLE001
            pass


def _single_instance(key: str):
    """Returns a QLocalServer for the first instance, or None after waking the running one."""
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(key)
    if sock.waitForConnected(400):
        sock.write(b"show")
        sock.waitForBytesWritten(400)
        sock.disconnectFromServer()
        return None
    QLocalServer.removeServer(key)  # left over after a crash
    server = QLocalServer()
    server.listen(key)
    return server


def selftest() -> int:
    """`XiControl.exe --selftest`: checks that a built copy has everything it needs (Qt widgets, video,
    images, encryption, HTTPS, QR codes) without opening a window. Exit code 0 = OK; details go to the log."""
    checks = {}

    def check(name, fn):
        try:
            fn()
            checks[name] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks[name] = f"FAILED: {exc!r}"

    def widgets():
        import gui.camera_view  # noqa: F401
        import gui.main_window  # noqa: F401

    def video():
        from PySide6.QtMultimedia import QAudioOutput, QMediaFormat, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        player = QMediaPlayer()
        player.setAudioOutput(QAudioOutput())
        player.setVideoOutput(QVideoWidget())
        if not QMediaFormat().supportedVideoCodecs(QMediaFormat.Decode):
            raise RuntimeError("no video decoders (FFmpeg plugin missing)")

    def images():
        from PySide6.QtGui import QImageReader
        fmts = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
        missing = {"png", "jpeg"} - fmts  # QR code and captcha pictures
        if missing:
            raise RuntimeError(f"image formats missing: {sorted(missing)}")

    def crypto():
        from xc import miio, secure
        c = miio.Crypto(bytes(16))
        assert c.decrypt(c.encrypt(b"x")) == b"x"
        assert secure.decrypt(secure.encrypt("x")) == "x"

    def https():
        import ssl  # noqa: F401
        import requests  # noqa: F401

    def qr_code():
        from xc import pairing
        if len(pairing.qr_matrix("b=selftest&s=eA==&p=eA==")) < 21:
            raise RuntimeError("the QR code library (segno) didn't produce a code")

    def recordings():
        from xc import camera
        if camera.decrypt_piece(bytes(16), bytes(16), bytes(16)) is None:
            raise RuntimeError("AES decryption is not available")

    for name, fn in (("widgets", widgets), ("video", video), ("images", images), ("encryption", crypto),
                     ("https", https), ("qr code", qr_code), ("recordings", recordings)):
        check(name, fn)
    for name, result in checks.items():
        log.info("selftest %-10s %s", name, result)
    return 0 if all(r == "ok" for r in checks.values()) else 1


def main():
    from xc import log as xclog
    from xc.config import Config, app_dir
    log_path = xclog.setup(app_dir(), console=sys.stderr is not None and not sys.executable.endswith("pythonw.exe"))
    sys.excepthook = lambda *exc: log.error("Unhandled error:\n%s", "".join(traceback.format_exception(*exc)))
    log.info("XiControl starting (Python %s)", sys.version.split()[0])
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        _fatal(f"The Qt library (PySide6) is missing or damaged:\n{exc}\n\nRun run.bat again to reinstall it.")
        return 1
    if sys.platform == "win32":
        try:  # own taskbar icon/group instead of python.exe
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("XiControl.App")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("XiControl")
    if "--selftest" in sys.argv[1:]:
        return selftest()
    server = _single_instance("XiControl-" + (os.environ.get("USERNAME") or os.environ.get("USER") or "user"))
    if server is None:
        log.info("XiControl is already running - brought it to the front")
        return 0
    from gui import theme
    from gui.icon import app_icon
    from gui.main_window import MainWindow
    app.setWindowIcon(app_icon())
    cfg = Config()
    theme.apply(app, cfg.data.get("theme", "light"))
    win = MainWindow(cfg)

    def wake():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.close()
        win.bring_to_front()
    server.newConnection.connect(wake)
    if "--tray" in sys.argv[1:] and win.tray.available:  # started with Windows: stay in the tray
        log.info("started hidden in the tray")
    else:
        win.show()
    log.info("log file: %s", log_path)
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        trace = traceback.format_exc()
        logging.getLogger("xicontrol").error("Start-up failed:\n%s", trace)
        _fatal(f"XiControl stopped because of an error:\n\n{trace[-1500:]}")
        sys.exit(1)
