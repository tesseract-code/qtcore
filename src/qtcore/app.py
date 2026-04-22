"""
app.py
PyQt6 Application bootstrap — org metadata, dock icon, splash screen,
and icon search-path registration for use with QtEventLoopManager.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QBuffer
from PyQt6.QtGui import QIcon, QPixmap, QColor, QPainter, QFont
from PyQt6.QtWidgets import QApplication, QSplashScreen, QWidget

from qtcore.utils import configure_high_dpi
from svg_icons.paths import LINE_ICONS, FILL_ICONS, OTHER_ICONS

# Map Qt search-path prefix  →  absolute directory
_ICON_SEARCH_PATHS: dict[str, Path] = {
    "line-icons": LINE_ICONS,
    "fill-icons": FILL_ICONS,
    "other-icons": OTHER_ICONS,
}


def _set_macos_dock_icon(pixmap: QPixmap) -> None:
    """
    Set the macOS dock icon from a QPixmap.
    Requires pyobjc-framework-Cocoa (pip install pyobjc-framework-Cocoa).
    Silently skips if pyobjc is not installed.
    """
    try:
        from AppKit import NSApplication, NSImage  # type: ignore
        from Foundation import NSData  # type: ignore
    except ImportError:
        return

    # Serialise the pixmap to PNG bytes — the only format NSImage reliably
    # accepts from raw data without needing a file on disk.
    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    png_bytes = buffer.data().data()  # QByteArray → bytes
    buffer.close()

    ns_data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
    ns_image = NSImage.alloc().initWithData_(ns_data)

    if ns_image is not None:
        NSApplication.sharedApplication().setApplicationIconImage_(ns_image)


def _set_macos_process_name(name: str) -> None:
    """
    Renames the process so macOS shows the app name in the menu bar
    instead of 'Python'.
    Two approaches are tried in order — both are needed for full coverage.
    """
    # 1. Cocoa: tells the window server / menu bar the display name
    try:
        from AppKit import NSBundle  # type: ignore
        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = name
    except ImportError:
        pass

    # 2. libc: renames the actual process (shows in Activity Monitor too)
    try:
        import ctypes, ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.pthread_setname_np(name.encode())
    except Exception:
        pass


class Application(QApplication):
    """
    QApplication subclass that wires up:
      • Organisation / application metadata
      • Qt icon search-path prefixes
      • A themed splash screen with optional progress text
    """

    def __init__(
            self,
            *,
            org_name: str = "",
            app_name: str = "QApp",
            app_version: str = "0.0.0",
            org_domain: str = "",
            argv: list[str] = None,
    ):
        configure_high_dpi()
        super().__init__(argv if argv is not None else sys.argv)

        self.org_name = org_name
        self.org_domain = org_domain or f"com.{org_name.lower()}"
        self.app_name = app_name
        self.app_version = app_version

        self._splash: Optional[QSplashScreen] = None

        self._apply_metadata()
        self._register_icon_paths()
        self._set_dock_icon()


    def _apply_metadata(self) -> None:
        self.setOrganizationName(self.org_name)
        self.setOrganizationDomain(self.org_domain)
        self.setApplicationName(self.app_name)
        self.setApplicationVersion(self.app_version)

        if sys.platform == "darwin":
            _set_macos_process_name(self.app_name)

    @staticmethod
    def _register_icon_paths() -> None:
        """Register every entry in _ICON_SEARCH_PATHS as a Qt search prefix."""
        from PyQt6.QtCore import QDir

        for prefix, directory in _ICON_SEARCH_PATHS.items():
            if directory.exists():
                QDir.addSearchPath(prefix, str(directory))
            else:
                # Non-fatal: warn but keep going so partial installs still work.
                import warnings
                warnings.warn(
                    f"Icon search path for '{prefix}' not found: {directory}",
                    stacklevel=2,
                )

    def _set_dock_icon(self) -> None:
        """
        Set the application (dock) icon.
        Uses 'app-icons:app.png' if present; falls back to a generated
        placeholder so a missing asset never crashes startup.
        """
        pixmap = QPixmap("app-icons:app.png")
        if pixmap.isNull():
            pixmap = _make_placeholder_icon(64, self.app_name[:1])

        icon = QIcon(pixmap)
        self.setWindowIcon(icon)

        # macOS dock icon
        if sys.platform == "darwin":
            _set_macos_dock_icon(pixmap)

    def show_splash(
            self,
            *,
            image_path: str = "app-icons:splash.png",
            min_display_ms: int = 1_500,
    ) -> QSplashScreen:
        """
        Show a splash screen.

        Parameters
        ----------
        image_path:
            Qt resource/search-path string for the splash image.
            Falls back to a generated placeholder when absent.
        min_display_ms:
            Minimum time (ms) the splash stays visible even if the main
            window is ready sooner.  Pass 0 to disable.

        Returns the QSplashScreen so callers can post status messages:
            app.show_splash().showMessage("Loading plugins…")
        """
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            pixmap = _make_splash_placeholder(480, 280, self.app_name,
                                              self.app_version)

        self._splash = QSplashScreen(
            pixmap,
            Qt.WindowType.WindowStaysOnTopHint,
        )
        self._splash.setFont(QFont("Inter", 10))
        self._splash.show()
        self.processEvents()

        if min_display_ms > 0:
            # Prevent finish() from hiding the splash until the timer fires
            _guard = _SplashGuard(self._splash, min_display_ms)
            self._splash._guard = _guard  # keep alive

        return self._splash

    def finish_splash(self, main_window: QWidget | None = None) -> None:
        """Hide the splash and reveal the main window."""
        if self._splash:
            self._splash.finish(main_window)
            self._splash = None


class _SplashGuard:
    """Prevents QSplashScreen.finish() from hiding the splash too early."""

    def __init__(self, splash: QSplashScreen, delay_ms: int):
        self._splash = splash
        self._ready = False
        QTimer.singleShot(delay_ms, self._on_ready)

        # Monkey-patch finish() to respect the timer
        _original_finish = splash.finish

        def _guarded_finish(window):
            if self._ready:
                _original_finish(window)
            else:
                self._pending_window = window
                self._original_finish = _original_finish

        splash.finish = _guarded_finish

    def _on_ready(self):
        self._ready = True
        if hasattr(self, "_pending_window"):
            self._original_finish(self._pending_window)


def _make_placeholder_icon(size: int, letter: str) -> QPixmap:
    """Generate a simple coloured square with a centred letter."""
    px = QPixmap(size, size)
    px.fill(QColor("#1a1a2e"))
    painter = QPainter(px)
    painter.setPen(QColor("white"))
    painter.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, letter.upper())
    painter.end()
    return px


def _make_splash_placeholder(
        w: int, h: int, name: str, version: str
) -> QPixmap:
    """Generate a minimal branded splash when no image asset is available."""
    px = QPixmap(w, h)
    px.fill(QColor("#1a1a2e"))
    painter = QPainter(px)

    # App name
    painter.setPen(QColor("white"))
    painter.drawText(px.rect().adjusted(0, -30, 0, 0),
                     Qt.AlignmentFlag.AlignCenter, name)

    # Version
    painter.setPen(QColor("#888888"))
    painter.drawText(px.rect().adjusted(0, 40, 0, 0),
                     Qt.AlignmentFlag.AlignCenter, f"v{version}")

    painter.end()
    return px
