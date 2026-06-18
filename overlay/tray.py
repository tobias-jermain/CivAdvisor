"""
CivAdvisor — system tray
========================

Keeps the app alive in the Windows system tray when the overlay is hidden, with
a right-click menu (Show / Settings / Logs / Quit) and double-click to toggle
the overlay. Falls back gracefully if the platform has no system tray.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QFont, QBrush
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

from theme import BG_TOP, ACCENT, TEXT_HI, CARD_BG3, BORDER

log = logging.getLogger("civadvisor")


def _fallback_icon() -> QIcon:
    """Draw a simple diamond glyph so the tray always has an icon."""
    pix = QPixmap(QSize(32, 32))
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(BG_TOP)))
    p.setPen(QColor(BORDER))
    p.drawRoundedRect(2, 2, 28, 28, 7, 7)
    p.setPen(QColor(ACCENT))
    f = QFont("Segoe UI", 15, QFont.Bold)
    p.setFont(f)
    p.drawText(pix.rect(), Qt.AlignCenter, "◆")
    p.end()
    return QIcon(pix)


class Tray(QSystemTrayIcon):
    def __init__(self, icon: QIcon, on_show, on_settings, on_logs, on_quit, parent=None):
        super().__init__(icon, parent)
        self._on_show = on_show
        self.setToolTip("CivAdvisor")

        menu = QMenu()
        menu.setStyleSheet(
            f"QMenu{{background:{CARD_BG3};color:{TEXT_HI};border:1px solid {BORDER};"
            f"border-radius:8px;padding:4px;}}"
            f"QMenu::item{{padding:6px 22px;border-radius:6px;font-size:12px;}}"
            f"QMenu::item:selected{{background:{ACCENT};color:#0E0E12;}}"
            f"QMenu::separator{{height:1px;background:{BORDER};margin:4px 8px;}}")

        def _act(label, slot):
            a = QAction(label, menu)
            a.triggered.connect(slot)
            menu.addAction(a)
            return a

        _act("Show overlay", on_show)
        _act("Settings", on_settings)
        _act("Live logs", on_logs)
        menu.addSeparator()
        _act("Quit CivAdvisor", on_quit)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._on_show()


def make_tray(app_icon, on_show, on_settings, on_logs, on_quit):
    """Build a Tray if the platform supports one, else return None."""
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("System tray not available on this platform.")
        return None
    icon = app_icon if (app_icon and not app_icon.isNull()) else _fallback_icon()
    tray = Tray(icon, on_show, on_settings, on_logs, on_quit)
    tray.show()
    log.info("System tray icon active.")
    return tray
