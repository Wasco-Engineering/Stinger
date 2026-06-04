"""Alicat short status packet parsing (pressure + setpoint only)."""

from __future__ import annotations

from app.hardware.alicat import AlicatController


def test_short_status_does_not_treat_setpoint_as_gauge() -> None:
    controller = AlicatController(
        {
            'address': 'A',
            'pressure_index': 0,
            'gauge_index': 1,
            'barometric_index': 2,
            'setpoint_index': 3,
        }
    )
    controller._send_command = lambda _cmd: 'A +013.53 +014.70 EXH'  # type: ignore[method-assign]

    reading = controller.read_status()
    assert reading is not None
    assert reading.pressure is not None
    assert reading.setpoint is not None
    assert reading.gauge_pressure is None
    assert reading.barometric_pressure is None
