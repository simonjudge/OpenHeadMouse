"""Cross-platform system-tray controls for dwell clicking."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .engine import DwellSettings
from .settings_dialog import DwellSettingsDialog


def _make_tray_icon() -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#ff0000"))
    painter.setBrush(QColor(255, 0, 0, 45))
    painter.drawEllipse(3, 3, 26, 26)
    painter.drawArc(3, 3, 26, 26, 90 * 16, -270 * 16)
    painter.end()
    return QIcon(pixmap)


class DwellTray(QObject):
    """Owns the tray icon and delegates state/settings changes to the app."""

    def __init__(
        self,
        service,
        settings: DwellSettings,
        *,
        on_settings_changed: Callable[[dict], None] | None = None,
        on_toggled: Callable[[bool], None] | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._service = service
        self._settings = settings
        self._on_settings_changed = on_settings_changed
        self._on_toggled = on_toggled
        self._settings_dialog: DwellSettingsDialog | None = None

        self.icon = QSystemTrayIcon(_make_tray_icon(), self)
        if hasattr(service, "error_occurred"):
            service.error_occurred.connect(self._show_error)
        if hasattr(service, "running_changed"):
            service.running_changed.connect(lambda _running: self._update_toggle_text())
        self.icon.setToolTip("Dwell Click")
        self.menu = QMenu()
        self.toggle_action = QAction(self.menu)
        self.toggle_action.triggered.connect(self._toggle)
        self.menu.addAction(self.toggle_action)
        self.settings_action = self.menu.addAction("Dwell Click Settings...")
        self.settings_action.triggered.connect(self.show_settings)
        self.menu.addSeparator()
        quit_action = self.menu.addAction("Quit")
        quit_action.triggered.connect(self._quit)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.menu.aboutToShow.connect(self._update_toggle_text)
        self._update_toggle_text()
        self.icon.show()

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_settings()

    def _show_error(self, message: str) -> None:
        self.icon.showMessage("Dwell Click", message, QSystemTrayIcon.MessageIcon.Warning)

    def set_settings(self, settings: DwellSettings) -> None:
        self._settings = settings

    def show_settings(self) -> None:
        if self._settings_dialog is not None:
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dialog = DwellSettingsDialog(self._settings)
        self._settings_dialog = dialog
        dialog.set_running(self._is_active())
        dialog.toggle_requested.connect(self._toggle)
        dialog.settings_applied.connect(self._settings_applied)
        dialog.finished.connect(lambda _result: self._clear_dialog(dialog))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _settings_applied(self, values: dict) -> None:
        if self._settings_dialog is not None:
            self._settings_dialog._zoom_options.set_values(values)
            self._settings_dialog._context_options.set_values(values)
        if self._on_settings_changed is not None:
            self._on_settings_changed(values)

    def _clear_dialog(self, dialog: DwellSettingsDialog) -> None:
        if self._settings_dialog is dialog:
            self._settings_dialog = None

    def _toggle(self) -> None:
        if self._is_active():
            self._service.stop()
        else:
            # Also resumes a service paused from the click menu.
            self._service.start()
        if self._on_toggled is not None:
            self._on_toggled(self._service.is_running)
        self._update_toggle_text()

    def _is_active(self) -> bool:
        return getattr(self._service, "is_active", self._service.is_running)

    def _update_toggle_text(self) -> None:
        if self._settings_dialog is not None:
            self._settings_dialog.set_running(self._is_active())
        self.toggle_action.setText(
            "Stop Dwell Clicking" if self._is_active() else "Start Dwell Clicking"
        )

    def _quit(self) -> None:
        from PySide6.QtWidgets import QApplication

        self._service.stop()
        QApplication.quit()

    def close(self) -> None:
        self._service.stop()
        self.icon.hide()
        if self._settings_dialog is not None:
            self._settings_dialog.close()
        self.deleteLater()
