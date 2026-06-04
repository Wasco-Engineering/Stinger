"""Simulated vacuum cycle: both edges and traverse limits (no GUI)."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.ptp_service import TestSetup
from app.services.test_executor import TestExecutor as _TestExecutor
from tests.test_executor_pressure import (
    _FlowPort,
    _FlowSimulator,
    _build_flow_executor,
    _flow_config,
)


def _vacuum_setup() -> TestSetup:
    """PSIA-scale bands like 17029 QAL16 (matches production logs)."""
    return TestSetup(
        part_id='17029',
        sequence_id='399',
        units_code='1',
        units_label='PSI',
        activation_direction='Decreasing',
        activation_target=8.3,
        pressure_reference='gauge',
        terminals={},
        bands={
            'increasing': {'lower': 10.0, 'upper': 11.0},
            'decreasing': {'lower': 7.8, 'upper': 8.8},
            'reset': {'lower': 7.0, 'upper': 12.0},
        },
        raw={},
    )


class _FlowPortWithEdges(_FlowPort):
    """Flow port that mirrors hardware Port edge detection."""

    def __init__(self, sim: _FlowSimulator) -> None:
        super().__init__(sim)
        self._edge_history: list[Any] = []
        self._last_switch_activated: bool | None = None

    def set_pressure(self, command_psi: float) -> bool:
        self.set_pressure_calls.append(command_psi)
        baro = self._sim.atmosphere_psi
        if command_psi < 0.0:
            self._sim.target_psi = command_psi + baro
        elif abs(command_psi) < 0.05:
            self._sim.target_psi = baro
        else:
            self._sim.target_psi = command_psi
        return True

    def read_precision_fast(self):
        from app.hardware.port import EdgeEvent, PortReading

        reading = self._sim.step()
        activated = bool(reading.switch and reading.switch.no_active)
        if self._last_switch_activated is not None and activated != self._last_switch_activated:
            self._edge_history.append(
                EdgeEvent(
                    pressure=float(reading.transducer.pressure if reading.transducer else 0.0),
                    timestamp=reading.timestamp,
                    direction='unknown',
                    activated=activated,
                )
            )
        self._last_switch_activated = activated
        return reading

    def get_edge_history(self) -> list[Any]:
        return list(self._edge_history)

    def clear_edge_history(self) -> None:
        self._edge_history.clear()
        self._last_switch_activated = None


def test_vacuum_single_cycle_detects_activation_and_deactivation() -> None:
    setup = _vacuum_setup()
    sim = _FlowSimulator(14.7, 7.8, 9.2, -1, 14.7, 14.7)
    port = _FlowPortWithEdges(sim)
    captured: dict[str, Any] = {'errors': []}
    executor = _TestExecutor(
        port_id='port_b',
        port=port,
        test_setup=setup,
        config=_flow_config(),
        get_latest_reading=lambda _pid: port.read_precision_fast(),
        get_barometric_psi=lambda _pid: sim.atmosphere_psi,
        on_error=lambda message: captured['errors'].append(message),
    )
    executor._lock_alicat_setpoint_reference()
    bounds = executor._resolve_sweep_bounds()
    executor._cycle_phase_runner.run_single_cycle('vacuum', bounds)

    assert captured['errors'] == [], captured['errors']
    assert len(port._edge_history) >= 1, port._edge_history
    assert len(executor._cycle_activation_samples) >= 1
    assert len(executor._cycle_deactivation_samples) >= 1
    vacuum_cmds = [c for c in port.set_pressure_calls if c > 0.1]
    assert min(vacuum_cmds) == pytest.approx(0.5, rel=0.1)
    assert any(abs(c) < 0.05 for c in port.set_pressure_calls)
