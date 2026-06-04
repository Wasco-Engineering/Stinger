"""Simulated quality-cal workflow tests (optional; not real hardware)."""

from __future__ import annotations

import pytest

pytest.importorskip('PyQt6')
from PyQt6.QtWidgets import QApplication

from quality_cal.core.hardware_helpers import prepare_port_for_target, set_vacuum_solenoid
from quality_cal.core.sim_port import SimulatedQualityPort
import threading


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.mark.simulated
def test_set_vacuum_solenoid_when_alicat_bleeds_atmosphere(qapp):
    port = SimulatedQualityPort()
    port._state.line_pressure_psia = 0.15
    port._state.setpoint_psia = 0.15
    port.daq.set_solenoid(True)

    assert port.set_solenoid(to_vacuum=True) is False
    assert set_vacuum_solenoid(port, 14.7) is True  # type: ignore[arg-type]
    assert port._state.vacuum_solenoid is True


@pytest.mark.simulated
def test_prepare_skips_vent_between_vacuum_points(qapp):
    port = SimulatedQualityPort()
    port._state.line_pressure_psia = 0.1
    port.daq.set_solenoid(True)
    cancel = threading.Event()

    ok, route, _ = prepare_port_for_target(
        port,  # type: ignore[arg-type]
        1.0,
        14.7,
        cancel,
        previous_route='vacuum',
    )
    assert ok
    assert route == 'vacuum'
    assert port.vent_count == 0
