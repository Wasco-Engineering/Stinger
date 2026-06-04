"""Focused TestExecutor pressure behavior tests."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.hardware.alicat import AlicatReading
from app.hardware.labjack import SwitchState, TransducerReading
from app.hardware.port import PortReading
from app.services.ptp_service import TestSetup, convert_pressure
from app.services.test_executor import TestExecutor as _TestExecutor
from tests.fixtures.pressure_data import build_port_reading


class _FakeAlicat:
    def __init__(self) -> None:
        self.configure_calls = 0
        self.cancel_hold_calls = 0

    def configure_units_from_ptp(self, _units_code: str) -> bool:
        self.configure_calls += 1
        return True

    def cancel_hold(self) -> bool:
        self.cancel_hold_calls += 1
        return True

    def set_ramp_rate(self, _rate: float) -> bool:
        return True


class _FakePort:
    def __init__(self, outcomes: list[bool]) -> None:
        self.alicat = _FakeAlicat()
        self._outcomes = outcomes
        self.vent_calls = 0

    def set_pressure(self, _setpoint: float) -> bool:
        if not self._outcomes:
            return True
        return self._outcomes.pop(0)

    def set_solenoid(self, to_vacuum: bool) -> bool:
        return True

    def vent_to_atmosphere(self) -> bool:
        self.vent_calls += 1
        return True


def _build_executor(port: _FakePort, get_latest_reading: Any = None, on_cancelled: Any = None) -> _TestExecutor:
    setup = TestSetup(
        part_id='17025',
        sequence_id='399',
        units_code='21',
        units_label='Torr',
        activation_direction='Decreasing',
        activation_target=400.0,
        pressure_reference='absolute',
        terminals={},
        bands={
            'increasing': {'lower': 550.0, 'upper': 600.0},
            'decreasing': {'lower': 400.0, 'upper': 500.0},
            'reset': {'lower': 300.0, 'upper': 350.0},
        },
        raw={},
    )
    return _TestExecutor(
        port_id='port_a',
        port=cast(Any, port),
        test_setup=setup,
        config={'control': {'cycling': {}, 'ramps': {}, 'edge_detection': {}, 'debounce': {}}},
        get_latest_reading=get_latest_reading or (lambda _pid: None),
        get_barometric_psi=lambda _pid: 14.7,
        on_cancelled=on_cancelled,
    )


def test_executor_set_pressure_recovers_after_one_failure() -> None:
    executor = _build_executor(_FakePort([False, True]))
    executor._set_pressure_or_raise(7.0)
    alicat = cast(_FakeAlicat, executor._port.alicat)
    assert alicat.configure_calls >= 1
    assert alicat.cancel_hold_calls == 1


def test_executor_set_pressure_raises_after_second_failure() -> None:
    executor = _build_executor(_FakePort([False, False]))
    with pytest.raises(RuntimeError):
        executor._set_pressure_or_raise(7.0)


def test_executor_run_emits_cancelled_and_vents() -> None:
    port = _FakePort([True])
    cancelled = {'called': False}
    executor = _build_executor(port, on_cancelled=lambda: cancelled.__setitem__('called', True))
    executor.request_cancel()
    executor._run()
    assert cancelled['called']
    assert port.vent_calls >= 1


def test_executor_sweep_to_edge_returns_none_without_switch_transition() -> None:
    port = _FakePort([True])
    reading = PortReading(
        transducer=TransducerReading(
            voltage=2.5,
            pressure=14.7,
            pressure_raw=14.7,
            pressure_reference='absolute',
            timestamp=0.0,
        ),
        alicat=AlicatReading(
            pressure=14.7,
            setpoint=14.7,
            timestamp=0.0,
            gauge_pressure=0.0,
            barometric_pressure=14.7,
        ),
        switch=SwitchState(no_active=False, nc_active=True, timestamp=0.0),
        timestamp=0.0,
    )
    executor = _build_executor(port, get_latest_reading=lambda _pid: reading)
    executor._edge_timeout_s = 0.05
    executor._stable_count = 2
    assert executor._sweep_to_edge(target_psi=0.0, direction=1) is None


def test_executor_sweep_to_edge_honors_post_target_grace_window() -> None:
    port = _FakePort([True])
    samples = [
        PortReading(
            transducer=TransducerReading(
                voltage=2.5,
                pressure=0.95,
                pressure_raw=0.95,
                pressure_reference='absolute',
                timestamp=0.00,
            ),
            alicat=AlicatReading(
                pressure=0.95,
                setpoint=1.00,
                timestamp=0.00,
                gauge_pressure=0.0,
                barometric_pressure=14.7,
            ),
            switch=SwitchState(no_active=False, nc_active=True, timestamp=0.00),
            timestamp=0.00,
        ),
        PortReading(
            transducer=TransducerReading(
                voltage=2.5,
                pressure=1.00,
                pressure_raw=1.00,
                pressure_reference='absolute',
                timestamp=0.02,
            ),
            alicat=AlicatReading(
                pressure=1.00,
                setpoint=1.00,
                timestamp=0.02,
                gauge_pressure=0.0,
                barometric_pressure=14.7,
            ),
            switch=SwitchState(no_active=False, nc_active=True, timestamp=0.02),
            timestamp=0.02,
        ),
        PortReading(
            transducer=TransducerReading(
                voltage=2.5,
                pressure=1.01,
                pressure_raw=1.01,
                pressure_reference='absolute',
                timestamp=0.04,
            ),
            alicat=AlicatReading(
                pressure=1.01,
                setpoint=1.00,
                timestamp=0.04,
                gauge_pressure=0.0,
                barometric_pressure=14.7,
            ),
            switch=SwitchState(no_active=True, nc_active=False, timestamp=0.04),
            timestamp=0.04,
        ),
        PortReading(
            transducer=TransducerReading(
                voltage=2.5,
                pressure=1.01,
                pressure_raw=1.01,
                pressure_reference='absolute',
                timestamp=0.06,
            ),
            alicat=AlicatReading(
                pressure=1.01,
                setpoint=1.00,
                timestamp=0.06,
                gauge_pressure=0.0,
                barometric_pressure=14.7,
            ),
            switch=SwitchState(no_active=True, nc_active=False, timestamp=0.06),
            timestamp=0.06,
        ),
    ]
    idx = {'value': -1}

    def _reading(_pid: str) -> PortReading:
        idx['value'] = min(idx['value'] + 1, len(samples) - 1)
        return samples[idx['value']]

    executor = _build_executor(port, get_latest_reading=_reading)
    executor._edge_timeout_s = 0.35
    executor._stable_count = 2
    executor._precision_post_target_grace_s = 0.15
    edge = executor._sweep_to_edge(target_psi=1.0, direction=1, edge_type='activation')
    assert edge is not None
    assert edge.activated is True


def test_precision_activation_accepts_right_port_vacuum_no_open_edge() -> None:
    """Right-port 17029 wiring activates when the NO sense line opens."""
    setup = TestSetup(
        part_id='17029',
        sequence_id='399',
        units_code='1',
        units_label='PSI',
        activation_direction='Decreasing',
        activation_target=8.3,
        pressure_reference='gauge',
        terminals={},
        bands={
            'increasing': {'lower': float('-inf'), 'upper': 11.0},
            'decreasing': {'lower': 7.8, 'upper': 8.8},
            'reset': {'lower': float('-inf'), 'upper': float('inf')},
        },
        raw={},
    )
    samples = [
        PortReading(
            transducer=TransducerReading(
                voltage=2.5,
                pressure=9.2,
                pressure_raw=9.2,
                pressure_reference='absolute',
                timestamp=0.0,
            ),
            alicat=AlicatReading(
                pressure=9.2,
                setpoint=9.2,
                timestamp=0.0,
                gauge_pressure=-5.5,
                barometric_pressure=14.7,
            ),
            switch=SwitchState(no_active=True, nc_active=False, timestamp=0.0),
            timestamp=0.0,
        ),
        PortReading(
            transducer=TransducerReading(
                voltage=2.5,
                pressure=8.2,
                pressure_raw=8.2,
                pressure_reference='absolute',
                timestamp=0.1,
            ),
            alicat=AlicatReading(
                pressure=8.2,
                setpoint=7.8,
                timestamp=0.1,
                gauge_pressure=-6.5,
                barometric_pressure=14.7,
            ),
            switch=SwitchState(no_active=False, nc_active=True, timestamp=0.1),
            timestamp=0.1,
        ),
        PortReading(
            transducer=TransducerReading(
                voltage=2.5,
                pressure=8.1,
                pressure_raw=8.1,
                pressure_reference='absolute',
                timestamp=0.2,
            ),
            alicat=AlicatReading(
                pressure=8.1,
                setpoint=7.8,
                timestamp=0.2,
                gauge_pressure=-6.6,
                barometric_pressure=14.7,
            ),
            switch=SwitchState(no_active=False, nc_active=True, timestamp=0.2),
            timestamp=0.2,
        ),
    ]
    idx = {'value': -1}

    def _reading(_pid: str) -> PortReading:
        idx['value'] = min(idx['value'] + 1, len(samples) - 1)
        return samples[idx['value']]

    executor = _TestExecutor(
        port_id='port_b',
        port=cast(Any, _FakePort([True])),
        test_setup=setup,
        config={
            'hardware': {'labjack': {'port_b': {'vacuum_switch_trips_on_no_open': True}}},
            'control': {
                'cycling': {},
                'ramps': {},
                'edge_detection': {'timeout_sec': 0.5},
                'debounce': {'stable_sample_count': 2, 'min_edge_interval_ms': 0},
            },
        },
        get_latest_reading=_reading,
        get_barometric_psi=lambda _pid: 14.7,
    )

    edge = executor._sweep_to_edge(target_psi=7.8, direction=-1, edge_type='activation')

    assert edge is not None
    assert edge.activated is False
    assert edge.pressure_psi == pytest.approx(8.2)


def test_cycle_activation_rejects_decreasing_vacuum_edge_above_ptp_band() -> None:
    setup = TestSetup(
        part_id='17021',
        sequence_id='399',
        units_code='21',
        units_label='Torr',
        activation_direction='Decreasing',
        activation_target=75.0,
        pressure_reference='absolute',
        terminals={},
        bands={
            'increasing': {'lower': float('-inf'), 'upper': 145.0},
            'decreasing': {'lower': 70.0, 'upper': 80.0},
            'reset': {'lower': float('-inf'), 'upper': float('inf')},
        },
        raw={},
    )
    executor = _TestExecutor(
        port_id='port_b',
        port=cast(Any, _FakePort([True])),
        test_setup=setup,
        config={
            'hardware': {'labjack': {'port_b': {'vacuum_switch_trips_on_no_open': True}}},
            'control': {
                'cycling': {},
                'ramps': {},
                'edge_detection': {'timeout_sec': 0.5},
                'debounce': {'stable_sample_count': 1, 'min_edge_interval_ms': 0},
            },
        },
        get_latest_reading=lambda _pid: None,
        get_barometric_psi=lambda _pid: 14.7,
    )
    executor._cycle_waiting_edge = 'activation'

    executor._observe_cycle_switch_sample(
        pressure_test_psi=9.5,
        switch_state=SwitchState(no_active=True, nc_active=False, timestamp=0.0),
    )
    executor._observe_cycle_switch_sample(
        pressure_test_psi=9.4,
        switch_state=SwitchState(no_active=False, nc_active=True, timestamp=0.1),
    )

    assert executor._cycle_activation_samples == []
    assert not executor._cycle_edge_pressure_allowed('activation', 3.05)
    assert executor._cycle_edge_pressure_allowed('activation', 1.45)


def test_executor_precision_targets_use_close_limit_for_decreasing() -> None:
    executor = _build_executor(_FakePort([True]))
    approach, target_out, target_back, source = executor._resolve_precision_targets(
        min_psi=convert_pressure(390.0, 'Torr', 'PSI'),
        max_psi=convert_pressure(600.0, 'Torr', 'PSI'),
        activation_direction=-1,
    )
    assert source == 'ptp-close-limit'
    assert approach == pytest.approx(convert_pressure(600.0, 'Torr', 'PSI'), rel=1e-6)
    assert target_out == pytest.approx(convert_pressure(400.0, 'Torr', 'PSI'), rel=1e-6)
    assert target_back == pytest.approx(convert_pressure(600.0, 'Torr', 'PSI'), rel=1e-6)


def test_executor_precision_targets_use_close_limit_for_increasing() -> None:
    setup = TestSetup(
        part_id='17025',
        sequence_id='399',
        units_code='1',
        units_label='PSI',
        activation_direction='Increasing',
        activation_target=25.0,
        pressure_reference='gauge',
        terminals={},
        bands={
            'increasing': {'lower': 24.0, 'upper': 26.0},
            'decreasing': {'lower': 22.0, 'upper': 23.0},
            'reset': {'lower': 21.0, 'upper': 27.0},
        },
        raw={},
    )
    executor = _TestExecutor(
        port_id='port_a',
        port=cast(Any, _FakePort([True])),
        test_setup=setup,
        config={'control': {'cycling': {}, 'ramps': {}, 'edge_detection': {}, 'debounce': {}}},
        get_latest_reading=lambda _pid: None,
        get_barometric_psi=lambda _pid: 14.7,
    )
    approach, target_out, target_back, source = executor._resolve_precision_targets(
        min_psi=22.0,
        max_psi=26.0,
        activation_direction=1,
    )
    assert source == 'ptp-close-limit'
    assert approach == pytest.approx(22.0, rel=1e-6)
    assert target_out == pytest.approx(26.0, rel=1e-6)
    assert target_back == pytest.approx(22.0, rel=1e-6)


def test_executor_precision_targets_auto_reorder_swapped_cycle_estimates() -> None:
    """When cycle estimates are in the wrong order for the activation direction,
    _ordered_cycle_estimates swaps them so that valid precision targets are
    derived from cycle data rather than falling back to PTP close-limit."""
    setup = TestSetup(
        part_id='17025',
        sequence_id='399',
        units_code='1',
        units_label='PSI',
        activation_direction='Increasing',
        activation_target=25.0,
        pressure_reference='gauge',
        terminals={},
        bands={
            'increasing': {'lower': 24.0, 'upper': 26.0},
            'decreasing': {'lower': 22.0, 'upper': 23.0},
            'reset': {'lower': 21.0, 'upper': 27.0},
        },
        raw={},
    )
    executor = _TestExecutor(
        port_id='port_a',
        port=cast(Any, _FakePort([True])),
        test_setup=setup,
        config={'control': {'cycling': {}, 'ramps': {}, 'edge_detection': {}, 'debounce': {}}},
        get_latest_reading=lambda _pid: None,
        get_barometric_psi=lambda _pid: 14.7,
    )
    # Raw labels are swapped for increasing direction (activation below deactivation),
    # but _ordered_cycle_estimates auto-corrects this.
    executor._cycle_activation_samples = [23.0]
    executor._cycle_deactivation_samples = [24.5]
    approach, target_out, target_back, source = executor._resolve_precision_targets(
        min_psi=22.0,
        max_psi=26.0,
        activation_direction=1,
    )
    assert source == 'cycle-estimate-offset-close-limit'
    # After reorder: activation_est=24.5 (higher), deactivation_est=23.0 (lower)
    offset = 40.0 * (14.7 / 760.0)  # precision_close_limit_offset_torr in PSI
    margin = 15.0 * (14.7 / 760.0)  # precision_deactivation_margin_torr in PSI
    assert approach == pytest.approx(max(22.0, 24.5 - offset), rel=1e-3)
    # target_out is widened to cover the activation band upper limit (26.0)
    # plus a 25% offset buffer to guarantee the sweep passes through
    assert target_out == pytest.approx(26.0 + offset * 0.25, rel=1e-3)
    assert target_back == pytest.approx(max(22.0, 23.0 - margin), rel=1e-3)


def test_executor_run_precision_does_not_skip_atmosphere_gate() -> None:
    executor = _build_executor(_FakePort([True]))
    captured: dict[str, bool] = {}

    executor._ensure_alicat_units = lambda: None
    executor._resolve_sweep_mode = lambda: 'pressure'
    executor._resolve_sweep_bounds = lambda: (0.0, 2.0)
    executor._cycle_phase_runner.run_pre_approach = lambda _mode, _bounds: None
    executor._run_single_cycle = lambda _mode, _bounds: None
    executor._run_precision_sweep = (
        lambda _mode, _bounds, skip_atmosphere_gate=False: (
            captured.__setitem__('skip_atmosphere_gate', skip_atmosphere_gate)
            or SimpleNamespace(activation_psi=1.2, deactivation_psi=0.8)
        )
    )

    executor._run()
    assert captured['skip_atmosphere_gate'] is False


@dataclass
class _FlowSimulator:
    atmosphere_psi: float
    activation_edge_psi: float
    deactivation_edge_psi: float
    activation_direction: int
    pressure_psi: float
    target_psi: float
    switch_activated: bool = False
    max_step_psi: float = 0.45
    tick: int = 0

    def step(self) -> PortReading:
        delta = self.target_psi - self.pressure_psi
        if abs(delta) <= self.max_step_psi:
            self.pressure_psi = self.target_psi
        elif delta > 0:
            self.pressure_psi += self.max_step_psi
        else:
            self.pressure_psi -= self.max_step_psi
        if self.activation_direction < 0:
            if not self.switch_activated and self.pressure_psi <= self.activation_edge_psi:
                self.switch_activated = True
            elif self.switch_activated and self.pressure_psi >= self.deactivation_edge_psi:
                self.switch_activated = False
        else:
            if not self.switch_activated and self.pressure_psi >= self.activation_edge_psi:
                self.switch_activated = True
            elif self.switch_activated and self.pressure_psi <= self.deactivation_edge_psi:
                self.switch_activated = False
        self.tick += 1
        ts = self.tick * 0.02
        return PortReading(
            transducer=TransducerReading(voltage=2.5, pressure=self.pressure_psi, pressure_raw=self.pressure_psi, pressure_reference='absolute', timestamp=ts),
            alicat=AlicatReading(pressure=self.pressure_psi, setpoint=self.target_psi, timestamp=ts, gauge_pressure=self.pressure_psi - self.atmosphere_psi, barometric_pressure=self.atmosphere_psi),
            switch=SwitchState(no_active=self.switch_activated, nc_active=not self.switch_activated, timestamp=ts),
            timestamp=ts,
        )


class _FlowAlicat:
    def configure_units_from_ptp(self, _units_code: str) -> bool:
        return True

    def cancel_hold(self) -> bool:
        return True

    def set_ramp_rate(self, _rate: float) -> bool:
        return True


class _FlowPort:
    def __init__(self, sim: _FlowSimulator) -> None:
        self._sim = sim
        self.alicat = _FlowAlicat()
        self.set_pressure_calls: list[float] = []

    def set_pressure(self, command_psi: float) -> bool:
        self.set_pressure_calls.append(command_psi)
        baro = self._sim.atmosphere_psi
        # Simulator tracks absolute line pressure; negative Alicat commands are PSIG.
        self._sim.target_psi = command_psi + baro if command_psi < 0.0 else command_psi
        return True

    def set_solenoid(self, to_vacuum: bool) -> bool:
        return True

    def vent_to_atmosphere(self) -> bool:
        self._sim.target_psi = self._sim.atmosphere_psi
        return True


def _flow_config() -> dict[str, Any]:
    return {
        'control': {
            'cycling': {'num_cycles': 3},
            'ramps': {'precision_sweep_rate_torr_per_sec': 18.0, 'precision_edge_rate_torr_per_sec': 18.0},
            'edge_detection': {'overshoot_beyond_limit_percent': 10.0, 'timeout_sec': 4.0},
            'debounce': {'stable_sample_count': 2, 'min_edge_interval_ms': 0},
        },
    }


def _build_flow_executor(setup: TestSetup, sim: _FlowSimulator) -> tuple[_TestExecutor, _FlowPort, dict[str, Any]]:
    port = _FlowPort(sim)
    captured: dict[str, Any] = {'cycling_complete': False, 'edges': None, 'errors': []}
    executor = _TestExecutor(
        port_id='port_b',
        port=cast(Any, port),
        test_setup=setup,
        config=_flow_config(),
        get_latest_reading=lambda _pid: sim.step(),
        get_barometric_psi=lambda _pid: sim.atmosphere_psi,
        on_cycling_complete=lambda: captured.__setitem__('cycling_complete', True),
        on_edges_captured=lambda a, d: captured.__setitem__('edges', (a, d)),
        on_error=lambda message: captured['errors'].append(message),
    )
    ptp_ref = str(setup.pressure_reference or 'absolute').strip().lower()
    executor._alicat_setpoint_ref = ptp_ref
    return executor, port, captured


def test_executor_control_pressure_prefers_transducer_over_stale_alicat() -> None:
    setup = TestSetup(
        part_id='17025',
        sequence_id='399',
        units_code='1',
        units_label='PSI',
        activation_direction='Increasing',
        activation_target=20.0,
        pressure_reference='absolute',
        terminals={},
        bands={},
        raw={},
    )
    sim = _FlowSimulator(14.7, 7.8, 9.2, -1, 14.7, 14.7)
    executor, _port, _captured = _build_flow_executor(setup, sim)
    reading = build_port_reading(transducer_pressure=12.0, alicat_pressure=25.0)
    assert executor._reading_pressure_abs_psi(reading) == pytest.approx(12.0)


def _run_full_flow_sim(port_key: str) -> None:
    setup = TestSetup(
        part_id='17025',
        sequence_id='399',
        units_code='21',
        units_label='Torr',
        activation_direction='Decreasing',
        activation_target=400.0,
        pressure_reference='absolute',
        terminals={},
        bands={'increasing': {'lower': 550.0, 'upper': 600.0}, 'decreasing': {'lower': 390.0, 'upper': 410.0}, 'reset': {'lower': 360.0, 'upper': 370.0}},
        raw={},
    )
    sim = _FlowSimulator(14.7, 7.8, 9.2, -1, 14.7, 14.7)
    executor, port, captured = _build_flow_executor(setup, sim)
    executor._port_id = port_key
    executor._run()
    assert captured['errors'] == [], f'{port_key}: {captured["errors"]}'
    assert captured['cycling_complete'] is True
    assert captured['edges'] is not None
    assert len(port.set_pressure_calls) >= 3


def test_executor_full_flow_cycle_and_precision_port_a() -> None:
    _run_full_flow_sim('port_a')


def test_executor_full_flow_cycle_and_precision_port_b() -> None:
    _run_full_flow_sim('port_b')


def test_executor_precision_failure_message_identifies_second_edge() -> None:
    setup = TestSetup(
        part_id='17025',
        sequence_id='399',
        units_code='21',
        units_label='Torr',
        activation_direction='Decreasing',
        activation_target=400.0,
        pressure_reference='absolute',
        terminals={},
        bands={},
        raw={},
    )
    errors: list[str] = []
    port = _FakePort([True])
    executor = _TestExecutor(
        port_id='port_b',
        port=cast(Any, port),
        test_setup=setup,
        config={'control': {'cycling': {'num_cycles': 1}, 'ramps': {}, 'edge_detection': {}, 'debounce': {}}},
        get_latest_reading=lambda _pid: None,
        get_barometric_psi=lambda _pid: 14.7,
        on_error=errors.append,
    )

    executor._ensure_alicat_units = lambda: None
    executor._resolve_sweep_mode = lambda: 'pressure'
    executor._resolve_sweep_bounds = lambda: (0.0, 2.0)
    executor._cycle_phase_runner.run_pre_approach = lambda _mode, _bounds: None
    executor._run_single_cycle = lambda _mode, _bounds: None

    def _force_second_edge_failure(
        _mode: str,
        _bounds: tuple[float, float],
        skip_atmosphere_gate: bool = False,
    ) -> None:
        del skip_atmosphere_gate
        executor._last_precision_missing_edge = 'second'
        return None

    executor._run_precision_sweep = _force_second_edge_failure
    executor._run()

    assert errors
    assert 'Deactivation edge not detected during precision return-sweep' in errors[0]
