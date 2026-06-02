#!/usr/bin/env python3
"""Small standalone UI to manually toggle exhaust solenoids per port."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.core.config import get_port_config, load_config
from app.hardware.labjack import LabJackController


def _fmt_psi(value: Optional[float]) -> str:
    if value is None:
        return '--'
    return f'{value:.2f} PSIA'


class SolenoidPortPanel(QFrame):
    """Controls and status for one port solenoid."""

    def __init__(self, port_id: str, dio: Optional[int], alicat_addr: str) -> None:
        super().__init__()
        self.port_id = port_id
        self.dio = dio
        self.setFrameShape(QFrame.Shape.StyledPanel)

        title = port_id.replace('_', ' ').title()
        dio_text = f'DIO{dio}' if dio is not None else 'not configured'
        header = QLabel(f'{title}  ·  Alicat {alicat_addr}  ·  {dio_text}')
        header.setStyleSheet('font-size: 15px; font-weight: 600;')
        self._state_label = QLabel('State: —')
        self._state_label.setStyleSheet('font-size: 14px;')
        self._transducer_label = QLabel('Transducer: --')
        self._alicat_label = QLabel('Alicat: --')
        for label in (self._transducer_label, self._alicat_label):
            label.setStyleSheet('font-family: Consolas, monospace; font-size: 13px;')

        self._vacuum_btn = QPushButton('Vacuum')
        self._atmosphere_btn = QPushButton('Atmosphere')
        for btn in (self._vacuum_btn, self._atmosphere_btn):
            btn.setMinimumHeight(52)
            btn.setStyleSheet('font-size: 14px; font-weight: 600;')
            btn.setEnabled(False)

        layout = QVBoxLayout()
        layout.addWidget(header)
        layout.addWidget(self._state_label)
        layout.addWidget(self._transducer_label)
        layout.addWidget(self._alicat_label)
        layout.addSpacing(8)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._vacuum_btn)
        btn_row.addWidget(self._atmosphere_btn)
        layout.addLayout(btn_row)
        self.setLayout(layout)

        self._vacuum_active = False

    def set_connected(self, connected: bool) -> None:
        self._vacuum_btn.setEnabled(connected and self.dio is not None)
        self._atmosphere_btn.setEnabled(connected and self.dio is not None)

    def set_state(self, to_vacuum: bool) -> None:
        self._vacuum_active = to_vacuum
        if to_vacuum:
            self._state_label.setText('State: VACUUM')
            self._state_label.setStyleSheet('font-size: 14px; color: #ffb347; font-weight: 600;')
        else:
            self._state_label.setText('State: ATMOSPHERE (safe)')
            self._state_label.setStyleSheet('font-size: 14px; color: #7dcea0; font-weight: 600;')

    def update_pressures(
        self,
        transducer_psi: Optional[float],
        alicat_psi: Optional[float],
    ) -> None:
        self._transducer_label.setText(f'Transducer: {_fmt_psi(transducer_psi)}')
        self._alicat_label.setText(f'Alicat: {_fmt_psi(alicat_psi)}')


class SolenoidTestWindow(QMainWindow):
    """Manual solenoid toggle tool for bench wiring checks."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle('Stinger Solenoid Test')
        self.resize(720, 360)

        self._controllers: Dict[str, LabJackController] = {}
        self._panels: Dict[str, SolenoidPortPanel] = {}
        self._connected = False

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_pressures)

        central = QWidget()
        root = QVBoxLayout()

        warn = QLabel(
            'Bench tool: toggles LabJack DIO outputs directly. '
            'Use ATMOSPHERE before high pressure. Vacuum only when near atmosphere.'
        )
        warn.setWordWrap(True)
        warn.setStyleSheet('color: #cccccc; padding: 4px;')
        root.addWidget(warn)

        row = QHBoxLayout()
        config = load_config()
        for port_id in ('port_a', 'port_b'):
            pc = get_port_config(config, port_id)
            lj = pc.get('labjack', {})
            ali = pc.get('alicat', {})
            panel = SolenoidPortPanel(
                port_id,
                lj.get('solenoid_dio'),
                str(ali.get('address', '?')),
            )
            self._panels[port_id] = panel
            row.addWidget(panel)
        root.addLayout(row)

        controls = QHBoxLayout()
        self._connect_btn = QPushButton('Connect LabJack')
        self._connect_btn.setMinimumHeight(44)
        self._connect_btn.clicked.connect(self._toggle_connection)
        controls.addWidget(self._connect_btn)

        self._all_atm_btn = QPushButton('All Atmosphere (Safe)')
        self._all_atm_btn.setMinimumHeight(44)
        self._all_atm_btn.clicked.connect(self._all_atmosphere)
        self._all_atm_btn.setEnabled(False)
        controls.addWidget(self._all_atm_btn)

        controls.addStretch(1)
        root.addLayout(controls)
        central.setLayout(root)
        self.setCentralWidget(central)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._set_status('Disconnected')

        for port_id, panel in self._panels.items():
            panel._vacuum_btn.clicked.connect(lambda _=False, p=port_id: self._set_vacuum(p, True))
            panel._atmosphere_btn.clicked.connect(lambda _=False, p=port_id: self._set_vacuum(p, False))

    def _set_status(self, text: str) -> None:
        self._status.showMessage(text)

    def _build_controller(self, port_id: str) -> LabJackController:
        config = load_config()
        port_cfg = get_port_config(config, port_id)
        merged = {**config['hardware']['labjack'], **port_cfg.get('labjack', {})}
        return LabJackController(merged)

    def _toggle_connection(self) -> None:
        if self._connected:
            self._disconnect()
            return
        try:
            self._controllers = {
                'port_a': self._build_controller('port_a'),
                'port_b': self._build_controller('port_b'),
            }
            if not self._controllers['port_a'].configure():
                raise RuntimeError(f"Port A: {self._controllers['port_a']._last_status}")
            if not self._controllers['port_b'].configure():
                raise RuntimeError(f"Port B: {self._controllers['port_b']._last_status}")

            self._all_atmosphere(silent=True)
            self._connected = True
            self._connect_btn.setText('Disconnect')
            self._all_atm_btn.setEnabled(True)
            for panel in self._panels.values():
                panel.set_connected(True)
            self._poll_timer.start(500)
            self._poll_once()
            self._set_status('Connected — solenoids at atmosphere')
        except Exception as exc:
            self._disconnect()
            QMessageBox.critical(self, 'Connect failed', str(exc))

    def _disconnect(self) -> None:
        self._poll_timer.stop()
        if self._connected:
            self._all_atmosphere(silent=True)
        for ctrl in self._controllers.values():
            try:
                ctrl.cleanup()
            except Exception:
                pass
        self._controllers = {}
        self._connected = False
        self._connect_btn.setText('Connect LabJack')
        self._all_atm_btn.setEnabled(False)
        for panel in self._panels.values():
            panel.set_connected(False)
            panel.set_state(False)
            panel.update_pressures(None, None)
        self._set_status('Disconnected')

    def _set_vacuum(self, port_id: str, to_vacuum: bool) -> None:
        if not self._connected:
            return
        ctrl = self._controllers.get(port_id)
        panel = self._panels.get(port_id)
        if ctrl is None or panel is None:
            return

        if to_vacuum:
            reply = QMessageBox.question(
                self,
                'Route to vacuum?',
                f'Route {port_id.replace("_", " ")} solenoid to VACUUM?\n\n'
                'Only do this near atmosphere with the vacuum pump ready.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        ok = ctrl.set_solenoid(to_vacuum)
        if not ok:
            QMessageBox.warning(
                self,
                'Solenoid command failed',
                f'{port_id}: {ctrl._last_status}',
            )
            return

        panel.set_state(to_vacuum)
        dio = panel.dio
        route = 'VACUUM' if to_vacuum else 'ATMOSPHERE'
        self._set_status(f'{port_id} DIO{dio} -> {route}')
        self._poll_once()

    def _all_atmosphere(self, silent: bool = False) -> None:
        if not self._controllers:
            return
        for port_id, ctrl in self._controllers.items():
            ctrl.set_solenoid_safe()
            panel = self._panels.get(port_id)
            if panel is not None:
                panel.set_state(False)
        if not silent:
            self._set_status('Both solenoids -> atmosphere')
        self._poll_once()

    def _poll_once(self) -> None:
        if not self._connected:
            return
        for port_id, ctrl in self._controllers.items():
            panel = self._panels.get(port_id)
            if panel is None:
                continue
            tr = ctrl.read_transducer()
            tr_psi = tr.pressure if tr else None
            panel.update_pressures(tr_psi, None)
        # Alicat optional — keep UI simple; transducer is enough for swap checks

    def _poll_pressures(self) -> None:
        self._poll_once()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._disconnect()
        super().closeEvent(event)


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough,
    )
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = SolenoidTestWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
