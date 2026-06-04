"""Unit tests for sweep planning helpers."""

from __future__ import annotations

import pytest

from app.services.ptp_service import TestSetup
from app.services.sweep_utils import (
    band_midpoint,
    narrow_bounds,
    resolve_cycle_ramp_targets,
    resolve_sweep_bounds,
    resolve_sweep_mode,
)


def _setup(units_label: str = 'PSI', activation_target: float | None = None) -> TestSetup:
    return TestSetup(
        part_id='P',
        sequence_id='S',
        units_code='1',
        units_label=units_label,
        activation_direction='Increasing',
        activation_target=activation_target,
        pressure_reference='absolute',
        terminals={},
        bands={
            'increasing': {'lower': 20.0, 'upper': 22.0},
            'decreasing': {'lower': 18.0, 'upper': 19.0},
            'reset': {'lower': 17.5, 'upper': 23.0},
        },
        raw={},
    )


def test_band_midpoint_handles_missing_inputs() -> None:
    assert band_midpoint(None) is None
    assert band_midpoint({}) is None
    assert band_midpoint({'lower': 1.0, 'upper': None}) is None
    assert band_midpoint({'lower': 2.0, 'upper': 4.0}) == 3.0


def test_resolve_sweep_mode_chooses_vacuum_when_target_below_atmosphere() -> None:
    setup = _setup(units_label='PSI', activation_target=5.0)
    assert resolve_sweep_mode(setup, atmosphere_psi=14.7) == 'vacuum'


def test_resolve_sweep_mode_defaults_to_pressure_without_target() -> None:
    assert resolve_sweep_mode(None, atmosphere_psi=14.7) == 'pressure'


def test_resolve_sweep_mode_vacuum_for_sub_atmospheric_gauge_bands() -> None:
    """17029-style QAL16: ref=gauge but limits are PSIA-scale vacuum values."""
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
    assert resolve_sweep_mode(setup, atmosphere_psi=14.7) == 'vacuum'


def test_resolve_sweep_mode_pressure_for_gauge_above_atmosphere() -> None:
    setup = TestSetup(
        part_id='P',
        sequence_id='S',
        units_code='1',
        units_label='PSI',
        activation_direction='Increasing',
        activation_target=25.0,
        pressure_reference='gauge',
        terminals={},
        bands={
            'increasing': {'lower': 24.0, 'upper': 26.0},
            'decreasing': {'lower': 22.0, 'upper': 23.0},
            'reset': {'lower': 20.0, 'upper': 30.0},
        },
        raw={},
    )
    assert resolve_sweep_mode(setup, atmosphere_psi=14.7) == 'pressure'


def test_resolve_sweep_bounds_uses_setup_when_available() -> None:
    setup = _setup()
    low, high = resolve_sweep_bounds(setup, fallback_port_cfg={})
    assert low == pytest.approx(17.5)
    assert high == pytest.approx(23.0)


def test_resolve_sweep_bounds_uses_fallback_when_setup_missing() -> None:
    low, high = resolve_sweep_bounds(
        None,
        fallback_port_cfg={'transducer_pressure_min': -2.0, 'transducer_pressure_max': 115.0},
    )
    assert low == -2.0
    assert high == 115.0


def test_resolve_cycle_ramp_targets_vacuum_returns_to_reset_edge() -> None:
    """Vacuum cycling should reset just past the upper band, not vent to baro."""
    act, deact = resolve_cycle_ramp_targets(
        sweep_mode='vacuum',
        activation_direction=-1,
        min_psi=7.8,
        max_psi=11.0,
        overshoot=0.5,
        barometric_psi=14.7,
        hw_min_psi=0.0,
        hw_max_psi=115.0,
        pressure_reference='gauge',
    )
    assert act == pytest.approx(0.5, rel=0.05)
    assert deact == pytest.approx(11.5, rel=1e-6)


def test_narrow_bounds_clamps_to_global_limits() -> None:
    low, high = narrow_bounds(
        activation_psi=10.0,
        deactivation_psi=20.0,
        min_bound=5.0,
        max_bound=23.0,
        factor=0.5,
        min_pad=1.0,
    )
    assert low == 5.0
    assert high == 23.0
