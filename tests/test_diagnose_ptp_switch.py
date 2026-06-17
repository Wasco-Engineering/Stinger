from __future__ import annotations

from scripts.diagnose_ptp_switch import configure_labjack_for_resolution, format_switch_state
from app.hardware.labjack import SwitchState
from app.services.ptp_switch_resolver import resolve_ptp_switch_config


class _FakeLabJack:
    def __init__(self) -> None:
        self.switch_nc_derived_from_no = False
        self.switch_no_derived_from_nc = False
        self.calls = []

    def configure_di_pins(self, *args, **kwargs) -> None:
        self.calls.append((args, kwargs))


def test_diagnostic_configures_fake_labjack_from_ptp_resolution() -> None:
    resolution = resolve_ptp_switch_config(
        ptp_params={
            'NormallyOpenTerminal': '1',
            'NormallyClosedTerminal': '3',
            'CommonTerminal': '4',
        },
        port_id='port_a',
        port_config={'switch_sensed_db9_pins': [3]},
    )
    labjack = _FakeLabJack()

    configure_labjack_for_resolution(labjack, resolution, com_state=0)

    assert labjack.calls == [((2, 2, 3), {'com_state': 0})]
    assert not labjack.switch_nc_derived_from_no
    assert labjack.switch_no_derived_from_nc


def test_diagnostic_formats_logical_switch_state() -> None:
    text = format_switch_state(SwitchState(no_active=True, nc_active=False, timestamp=1.0))

    assert 'no_active=True' in text
    assert 'nc_active=False' in text
    assert 'switch_activated=True' in text
    assert 'valid=True' in text
