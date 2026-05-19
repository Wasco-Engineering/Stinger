#!/usr/bin/env python3
"""Realtime GUI for monitoring both transducers simultaneously."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
for path in (str(PROJECT_ROOT), str(SCRIPT_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.core.config import load_config
from manual_transducer_monitor import (
    MonitorSample,
    build_alicat_controller,
    build_labjack_controller,
    collect_sample,
)


def _fmt(value: Optional[float], decimals: int = 3, suffix: str = "") -> str:
    if value is None:
        return "--"
    return f"{value:.{decimals}f}{suffix}"


@dataclass
class PortHardware:
    """Controllers required for one live monitor port."""

    labjack: object
    alicat: object


class PortPanel(QGroupBox):
    """Simple card showing live data for one port."""

    def __init__(self, port_id: str) -> None:
        super().__init__(port_id.replace("_", " ").upper())
        self._value_labels: Dict[str, QLabel] = {}

        layout = QGridLayout()
        rows = [
            ("AIN+ to GND", "ain_pos"),
            ("AIN- to GND", "ain_neg"),
            ("Differential", "diff"),
            ("SE Delta", "se_delta"),
            ("Transducer Volts", "transducer_v"),
            ("Transducer PSIA", "transducer_psia"),
            ("Alicat PSIA", "alicat_psia"),
            ("Alicat PSIG", "alicat_psig"),
            ("Barometric", "baro"),
            ("Setpoint", "setpoint"),
            ("Offset", "offset"),
            ("Elapsed", "elapsed"),
        ]
        for row_index, (title, key) in enumerate(rows):
            title_label = QLabel(f"{title}:")
            value_label = QLabel("--")
            value_label.setStyleSheet("font-family: Consolas, monospace;")
            layout.addWidget(title_label, row_index, 0)
            layout.addWidget(value_label, row_index, 1)
            self._value_labels[key] = value_label

        self.setLayout(layout)

    def clear(self) -> None:
        for label in self._value_labels.values():
            label.setText("--")

    def update_sample(self, sample: Optional[MonitorSample]) -> None:
        if sample is None:
            self.clear()
            return

        offset = None
        if sample.transducer_psia is not None and sample.alicat_psia is not None:
            offset = sample.transducer_psia - sample.alicat_psia

        values = {
            "ain_pos": _fmt(sample.ain_pos_single_ended_v, 4, " V"),
            "ain_neg": _fmt(sample.ain_neg_single_ended_v, 4, " V"),
            "diff": _fmt(sample.differential_v, 4, " V"),
            "se_delta": _fmt(sample.single_ended_delta_v, 4, " V"),
            "transducer_v": _fmt(sample.transducer_voltage_v, 4, " V"),
            "transducer_psia": _fmt(sample.transducer_psia, 3, " PSIA"),
            "alicat_psia": _fmt(sample.alicat_psia, 3, " PSIA"),
            "alicat_psig": _fmt(sample.alicat_psig, 3, " PSIG"),
            "baro": _fmt(sample.barometric_psia, 3, " PSIA"),
            "setpoint": _fmt(sample.alicat_setpoint_psia, 3, " PSIA"),
            "offset": _fmt(offset, 3, " PSI"),
            "elapsed": _fmt(sample.elapsed_s, 1, " s"),
        }
        for key, text in values.items():
            self._value_labels[key].setText(text)


class DualPortMonitorWindow(QMainWindow):
    """Standalone window for both transducer channels."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Stinger Dual Port Transducer Monitor")
        self.resize(900, 520)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_once)

        self._config = None
        self._hardware: Dict[str, PortHardware] = {}
        self._start_time: Optional[float] = None

        central = QWidget()
        root_layout = QVBoxLayout()

        controls_layout = QHBoxLayout()
        self._start_button = QPushButton("Start")
        self._start_button.clicked.connect(self.start_monitoring)
        controls_layout.addWidget(self._start_button)

        self._stop_button = QPushButton("Stop")
        self._stop_button.clicked.connect(self.stop_monitoring)
        self._stop_button.setEnabled(False)
        controls_layout.addWidget(self._stop_button)

        self._exhaust_button = QPushButton("Exhaust Both")
        self._exhaust_button.clicked.connect(self.exhaust_both)
        self._exhaust_button.setEnabled(False)
        controls_layout.addWidget(self._exhaust_button)

        self._tare_button = QPushButton("Tare Both")
        self._tare_button.clicked.connect(self.tare_both)
        self._tare_button.setEnabled(False)
        controls_layout.addWidget(self._tare_button)

        controls_layout.addWidget(QLabel("Poll (ms):"))
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(100, 5000)
        self._interval_spin.setSingleStep(100)
        self._interval_spin.setValue(500)
        controls_layout.addWidget(self._interval_spin)
        controls_layout.addStretch(1)
        root_layout.addLayout(controls_layout)

        panels_layout = QHBoxLayout()
        self._panels = {
            "port_a": PortPanel("port_a"),
            "port_b": PortPanel("port_b"),
        }
        panels_layout.addWidget(self._panels["port_a"])
        panels_layout.addWidget(self._panels["port_b"])
        root_layout.addLayout(panels_layout)

        central.setLayout(root_layout)
        self.setCentralWidget(central)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._set_status("Ready")

    def _set_status(self, text: str) -> None:
        self._status.showMessage(text)

    def _set_running_state(self, running: bool) -> None:
        self._start_button.setEnabled(not running)
        self._stop_button.setEnabled(running)
        self._exhaust_button.setEnabled(running)
        self._tare_button.setEnabled(running)

    def start_monitoring(self) -> None:
        if self._timer.isActive():
            return

        try:
            self._config = load_config()
            port_a = self._build_port_hardware("port_a")
            port_b = self._build_port_hardware("port_b")
            self._connect_hardware(port_a, port_b)
            self._hardware = {
                "port_a": port_a,
                "port_b": port_b,
            }
            self._start_time = time.perf_counter()
            self._timer.start(self._interval_spin.value())
            self._set_running_state(True)
            self._set_status("Monitoring both ports")
            self._poll_once()
        except Exception as exc:
            self._cleanup_hardware()
            self._set_status(f"Start failed: {exc}")

    def stop_monitoring(self) -> None:
        self._timer.stop()
        self._cleanup_hardware()
        for panel in self._panels.values():
            panel.clear()
        self._set_running_state(False)
        self._set_status("Stopped")

    def exhaust_both(self) -> None:
        try:
            for hardware in self._hardware.values():
                hardware.alicat.exhaust()
            self._set_status("Sent EXH to both Alicats")
        except Exception as exc:
            self._set_status(f"Exhaust failed: {exc}")

    def tare_both(self) -> None:
        try:
            for hardware in self._hardware.values():
                hardware.alicat.tare()
            self._set_status("Sent tare to both Alicats")
        except Exception as exc:
            self._set_status(f"Tare failed: {exc}")

    def _build_port_hardware(self, port_id: str) -> PortHardware:
        if self._config is None:
            raise RuntimeError("Configuration not loaded")

        labjack = build_labjack_controller(self._config, port_id)
        alicat = build_alicat_controller(self._config, port_id)
        alicat._auto_tare_on_connect = False
        return PortHardware(labjack=labjack, alicat=alicat)

    def _connect_hardware(self, port_a: PortHardware, port_b: PortHardware) -> None:
        if not port_a.labjack.configure():
            raise RuntimeError(f"Port A LabJack configure failed: {port_a.labjack._last_status}")
        if not port_b.labjack.configure():
            raise RuntimeError(f"Port B LabJack configure failed: {port_b.labjack._last_status}")

        if not port_a.alicat.connect():
            raise RuntimeError(f"Port A Alicat connect failed: {port_a.alicat._last_status}")

        same_port = port_a.alicat.com_port == port_b.alicat.com_port
        if same_port and port_a.alicat._serial is not None:
            port_b.alicat.set_shared_serial(port_a.alicat._serial)
        elif not port_b.alicat.connect():
            raise RuntimeError(f"Port B Alicat connect failed: {port_b.alicat._last_status}")

    def _poll_once(self) -> None:
        if self._start_time is None:
            return

        try:
            for port_id, hardware in self._hardware.items():
                sample = collect_sample(
                    hardware.labjack,
                    hardware.alicat,
                    self._start_time,
                )
                self._panels[port_id].update_sample(sample)
            self._set_status(f"Updated {time.strftime('%H:%M:%S')}")
        except Exception as exc:
            self._set_status(f"Polling failed: {exc}")
            self.stop_monitoring()

    def _cleanup_hardware(self) -> None:
        for hardware in self._hardware.values():
            try:
                hardware.alicat.disconnect()
            except Exception:
                pass
            try:
                hardware.labjack.cleanup()
            except Exception:
                pass
        self._hardware = {}
        self._start_time = None

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.stop_monitoring()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = DualPortMonitorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
