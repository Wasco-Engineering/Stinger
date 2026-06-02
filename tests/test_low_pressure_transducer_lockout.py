from __future__ import annotations

import pytest

from app.services.ptp_service import TestSetup
from app.services.work_order_controller import WorkOrderController


def _setup(activation_target: float, units_label: str) -> TestSetup:
    return TestSetup(
        part_id='PART-1',
        sequence_id='300',
        units_code=None,
        units_label=units_label,
        activation_direction='Increasing',
        activation_target=activation_target,
        pressure_reference='absolute',
        terminals={},
        bands={},
        raw={},
    )


def _controller(*, activation_target: float, units_label: str, installed: bool):
    controller = WorkOrderController.__new__(WorkOrderController)
    controller._current_test_setup = _setup(activation_target, units_label)
    controller._config = {
        'hardware': {
            'labjack': {
                'port_a': {
                    'transducer_installed': installed,
                },
            },
        },
    }
    return controller


@pytest.mark.parametrize(
    ('value', 'units_label'),
    [
        (49.0, 'Torr'),
        (49000.0, 'mTorr'),
        (49.0, 'mmHg'),
        (25.0, 'Torr'),
    ],
)
def test_low_pressure_target_blocks_when_transducer_not_installed(value, units_label) -> None:
    controller = _controller(
        activation_target=value,
        units_label=units_label,
        installed=False,
    )

    activation_torr = controller._activation_target_torr()
    assert activation_torr is not None
    assert activation_torr < 50.0
    assert controller._is_low_pressure_transducer_locked_out('port_a') is True


def test_low_pressure_target_allows_when_transducer_installed() -> None:
    controller = _controller(
        activation_target=49.0,
        units_label='Torr',
        installed=True,
    )

    assert controller._is_low_pressure_transducer_locked_out('port_a') is False


def test_target_at_or_above_threshold_is_not_blocked() -> None:
    controller = _controller(
        activation_target=50.0,
        units_label='Torr',
        installed=False,
    )

    assert controller._is_low_pressure_transducer_locked_out('port_a') is False
