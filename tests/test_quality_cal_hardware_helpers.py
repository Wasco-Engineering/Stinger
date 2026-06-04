"""Tests for quality calibration hardware helper logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from quality_cal.core.hardware_helpers import (
    feedback_pressure_psia,
    infer_barometric_psia,
    port_on_vacuum_from_reading,
    transducer_trusted_for_safety,
)


@dataclass
class _FakeTransducer:
    pressure: float
    pressure_reference: str = 'absolute'
    pressure_raw: float | None = None


@dataclass
class _FakeAlicat:
    pressure: Optional[float]
    gauge_pressure: Optional[float] = None
    barometric_pressure: Optional[float] = None


@dataclass
class _FakeReading:
    alicat: Optional[_FakeAlicat]
    transducer: Optional[_FakeTransducer]


def test_feedback_pressure_prefers_transducer_only_below_5_psia_on_vacuum():
    reading = _FakeReading(
        alicat=_FakeAlicat(pressure=13.5),
        transducer=_FakeTransducer(pressure=9.8),
    )
    feedback = feedback_pressure_psia(
        reading,  # type: ignore[arg-type]
        target_psia=10.0,
        barometric_psia=14.7,
        route='vacuum',
    )
    assert feedback == 9.8

    deep = _FakeReading(
        alicat=_FakeAlicat(pressure=13.5),
        transducer=_FakeTransducer(pressure=1.0),
    )
    assert (
        feedback_pressure_psia(  # type: ignore[arg-type]
            deep,
            target_psia=1.0,
            barometric_psia=14.7,
            route='vacuum',
        )
        == 1.0
    )


def test_feedback_pressure_prefers_transducer_for_sub_atmospheric_targets():
    reading = _FakeReading(
        alicat=_FakeAlicat(pressure=13.5),
        transducer=_FakeTransducer(pressure=13.5),
    )
    feedback = feedback_pressure_psia(
        reading,  # type: ignore[arg-type]
        target_psia=1.0,
        barometric_psia=14.7,
        route='vacuum',
    )
    assert feedback == 13.5


def test_feedback_pressure_uses_transducer_on_vacuum_when_alicat_bleeds():
    reading = _FakeReading(
        alicat=_FakeAlicat(pressure=13.5),
        transducer=_FakeTransducer(pressure=0.8),
    )
    feedback = feedback_pressure_psia(
        reading,  # type: ignore[arg-type]
        target_psia=1.0,
        barometric_psia=14.7,
        route='vacuum',
    )
    assert feedback == 0.8


def test_infer_barometric_ignores_setpoint_masquerading_as_gauge():
    reading = _FakeReading(
        alicat=_FakeAlicat(pressure=13.55, gauge_pressure=5.0),
        transducer=_FakeTransducer(pressure=13.5),
    )
    assert infer_barometric_psia(reading) == 14.7  # type: ignore[arg-type]


def test_port_on_vacuum_false_near_atmosphere():
    reading = _FakeReading(
        alicat=_FakeAlicat(pressure=13.5),
        transducer=_FakeTransducer(pressure=12.5),
    )
    assert port_on_vacuum_from_reading(reading, 14.7) is False  # type: ignore[arg-type]


def test_port_on_vacuum_true_at_low_pressure():
    reading = _FakeReading(
        alicat=_FakeAlicat(pressure=13.5),
        transducer=_FakeTransducer(pressure=0.8),
    )
    assert port_on_vacuum_from_reading(reading, 14.7) is True  # type: ignore[arg-type]


def test_feedback_pressure_uses_alicat_when_transducer_saturated():
    class _FakeDaq:
        pressure_max = 30.0

    class _FakePort:
        daq = _FakeDaq()

    reading = _FakeReading(
        alicat=_FakeAlicat(pressure=92.0),
        transducer=_FakeTransducer(pressure=30.32),
    )
    feedback = feedback_pressure_psia(
        reading,  # type: ignore[arg-type]
        target_psia=115.0,
        barometric_psia=14.7,
        route='pressure',
        port=_FakePort(),  # type: ignore[arg-type]
    )
    assert feedback == 92.0


def test_transducer_not_trusted_when_pegged_at_range_max():
    class _FakeDaq:
        pressure_max = 30.0

    class _FakePort:
        daq = _FakeDaq()

    reading = _FakeReading(
        alicat=_FakeAlicat(pressure=13.5),
        transducer=_FakeTransducer(pressure=30.32),
    )
    assert transducer_trusted_for_safety(
        reading,  # type: ignore[arg-type]
        14.7,
        port=_FakePort(),  # type: ignore[arg-type]
    ) is False


def test_feedback_pressure_uses_alicat_on_pressure_route():
    reading = _FakeReading(
        alicat=_FakeAlicat(pressure=25.0),
        transducer=_FakeTransducer(pressure=24.5),
    )
    feedback = feedback_pressure_psia(
        reading,  # type: ignore[arg-type]
        target_psia=25.0,
        barometric_psia=14.7,
        route='pressure',
    )
    assert feedback == 25.0
