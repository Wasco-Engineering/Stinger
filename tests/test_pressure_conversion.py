"""Focused pressure conversion and UI display behavior tests."""

from __future__ import annotations

import pytest

from app.services.pressure_domain import (
    infer_setpoint_reference,
    resolve_alicat_setpoint_reference_for_test,
    to_alicat_setpoint_psi,
)
from app.services.ptp_service import build_pressure_visualization, convert_pressure, derive_test_setup
from app.services.ui_bridge import UIBridge
from app.services.work_order_controller import _is_plausible_barometric_psi
from tests.fixtures.pressure_data import build_port_reading


def test_convert_pressure_round_trip_torr() -> None:
    torr_value = convert_pressure(14.7, 'PSI', 'Torr')
    assert torr_value == pytest.approx(760.0, rel=1e-3)
    assert convert_pressure(torr_value, 'Torr', 'PSI') == pytest.approx(14.7, rel=1e-3)


def test_debug_setpoint_recomputes_when_switching_psig_psia() -> None:
    bridge = UIBridge({})
    events: list[tuple[str, float, float | None, float | None, float | None]] = []
    bridge.debug_chart_updated.connect(
        lambda port_id, ts, pressure, setpoint, alicat: events.append((port_id, ts, pressure, setpoint, alicat))
    )
    bridge.set_pressure_unit('PSIG')
    bridge.update_pressure(
        'port_a',
        build_port_reading(
            timestamp=1.0,
            transducer_pressure=9.7,
            transducer_reference='absolute',
            alicat_pressure=9.7,
            alicat_setpoint=9.7,
            barometric_pressure=14.7,
            gauge_pressure=-5.0,
        ),
    )
    assert events[-1][3] == pytest.approx(-5.0, rel=1e-3)
    bridge.set_pressure_unit('PSIA')
    assert events[-1][3] == pytest.approx(9.7, rel=1e-3)


def test_set_pressure_accepts_gauge_input_for_absolute_display() -> None:
    bridge = UIBridge({})
    events: list[tuple[str, float, str]] = []
    bridge.pressure_updated.connect(lambda port_id, pressure, unit: events.append((port_id, pressure, unit)))
    bridge.set_pressure_unit('PSIA')
    bridge.set_pressure('port_a', -5.0, 'PSIG')
    assert events[-1][1] == pytest.approx(9.7, rel=1e-3)
    assert events[-1][2] == 'PSIA'


def test_debug_setpoint_uses_inferred_barometric_when_direct_value_missing() -> None:
    bridge = UIBridge({})
    events: list[tuple[str, float, float | None, float | None, float | None]] = []
    bridge.debug_chart_updated.connect(
        lambda port_id, ts, pressure, setpoint, alicat: events.append((port_id, ts, pressure, setpoint, alicat))
    )
    reading = build_port_reading(
        timestamp=1.0,
        transducer_pressure=1.0,
        transducer_reference='absolute',
        alicat_pressure=1.0,
        alicat_setpoint=-12.6,
        barometric_pressure=0.0,
        gauge_pressure=-12.6,
    )
    assert reading.alicat is not None
    reading.alicat.barometric_pressure = None
    bridge.set_pressure_unit('PSIA')
    bridge.update_pressure('port_a', reading)
    assert events[-1][3] == pytest.approx(1.0, rel=1e-3)


def test_derive_setup_defaults_missing_pressure_reference_to_absolute_for_torr() -> None:
    setup = derive_test_setup(
        '17025',
        '399',
        {
            'ActivationTarget': '400.000000',
            'IncreasingLowerLimit': '390.000000',
            'IncreasingUpperLimit': '410.000000',
            'DecreasingLowerLimit': '380.000000',
            'DecreasingUpperLimit': '395.000000',
            'ResetBandLowerLimit': '360.000000',
            'ResetBandUpperLimit': '370.000000',
            'TargetActivationDirection': 'Decreasing',
            'UnitsOfMeasure': '21',
            'CommonTerminal': '3',
            'NormallyOpenTerminal': '2',
            'NormallyClosedTerminal': '1',
        },
    )
    assert setup.units_label == 'Torr'
    assert setup.pressure_reference == 'absolute'


def test_derive_setup_normalizes_pressure_reference_alias() -> None:
    setup = derive_test_setup(
        '17025',
        '399',
        {
            'ActivationTarget': '400.000000',
            'IncreasingLowerLimit': '390.000000',
            'IncreasingUpperLimit': '410.000000',
            'DecreasingLowerLimit': '380.000000',
            'DecreasingUpperLimit': '395.000000',
            'ResetBandLowerLimit': '360.000000',
            'ResetBandUpperLimit': '370.000000',
            'TargetActivationDirection': 'Decreasing',
            'UnitsOfMeasure': '21',
            'PressureReference': 'Gage',
            'CommonTerminal': '3',
            'NormallyOpenTerminal': '2',
            'NormallyClosedTerminal': '1',
        },
    )
    assert setup.pressure_reference == 'gauge'


@pytest.mark.parametrize(
    ('direction', 'activation_label', 'deactivation_label'),
    [
        ('Increasing', 'ACT/INC', 'DEACT/DEC'),
        ('Decreasing', 'ACT/DEC', 'DEACT/INC'),
    ],
)
def test_pressure_visualization_labels_follow_activation_direction(
    direction: str,
    activation_label: str,
    deactivation_label: str,
) -> None:
    setup = derive_test_setup(
        '17025',
        '399',
        {
            'ActivationTarget': '400.000000',
            'IncreasingLowerLimit': '390.000000',
            'IncreasingUpperLimit': '410.000000',
            'DecreasingLowerLimit': '380.000000',
            'DecreasingUpperLimit': '395.000000',
            'ResetBandLowerLimit': '360.000000',
            'ResetBandUpperLimit': '370.000000',
            'TargetActivationDirection': direction,
            'UnitsOfMeasure': '21',
            'PressureReference': 'Absolute',
            'CommonTerminal': '3',
            'NormallyOpenTerminal': '2',
            'NormallyClosedTerminal': '1',
        },
    )

    viz = build_pressure_visualization(setup, {})

    assert viz['activation_label'] == activation_label
    assert viz['deactivation_label'] == deactivation_label


def test_barometric_plausibility_guard() -> None:
    assert _is_plausible_barometric_psi(14.7)
    assert not _is_plausible_barometric_psi(0.2635)


def test_to_alicat_setpoint_psi_gauge_reference() -> None:
    assert to_alicat_setpoint_psi(7.8, barometric_psi=14.7, setpoint_reference='gauge') == pytest.approx(
        7.8, rel=1e-6
    )
    assert to_alicat_setpoint_psi(14.7, barometric_psi=14.7, setpoint_reference='gauge') == pytest.approx(
        0.0, rel=1e-6
    )


def test_to_alicat_setpoint_psi_absolute_reference() -> None:
    assert to_alicat_setpoint_psi(7.8, barometric_psi=14.7, setpoint_reference='absolute') == pytest.approx(
        7.8, rel=1e-6
    )


def test_resolve_alicat_setpoint_reference_for_test_uses_ptp_gauge() -> None:
    reading = build_port_reading(
        alicat_pressure=13.5,
        alicat_setpoint=14.7,
        barometric_pressure=14.7,
    )
    assert (
        resolve_alicat_setpoint_reference_for_test(
            ptp_pressure_reference='gauge',
            reading=reading,
            barometric_psi=14.7,
        )
        == 'gauge'
    )


def test_infer_setpoint_reference_sub_atmospheric_psia_while_at_atmosphere() -> None:
    assert (
        infer_setpoint_reference(
            setpoint=7.8,
            absolute_pressure=14.5,
            gauge_pressure=-0.2,
            barometric_psi=14.7,
        )
        == 'absolute'
    )
    assert (
        infer_setpoint_reference(
            setpoint=-6.9,
            absolute_pressure=14.5,
            gauge_pressure=-0.2,
            barometric_psi=14.7,
        )
        == 'gauge'
    )
