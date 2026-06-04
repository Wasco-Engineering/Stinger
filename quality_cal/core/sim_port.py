"""Simulated port for headless quality-cal workflow tests."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from app.hardware.alicat import AlicatReading
from app.hardware.labjack import TransducerReading
from app.hardware.port import PortId, PortReading


@dataclass
class _SimState:
    barometric_psia: float = 14.7
    line_pressure_psia: float = 14.7
    setpoint_psia: float = 14.7
    vacuum_solenoid: bool = False
    exhaust: bool = False
    vent_count: int = 0


class _SimDaq:
    def __init__(self, state: _SimState) -> None:
        self._state = state

    def get_status(self) -> dict[str, Any]:
        return {'simulated': True, 'status': 'simulated'}

    def set_solenoid(self, to_vacuum: bool) -> bool:
        self._state.vacuum_solenoid = to_vacuum
        if not to_vacuum:
            self._state.line_pressure_psia = min(
                self._state.line_pressure_psia + 2.0,
                self._state.barometric_psia,
            )
        return True

    def reset_filter(self) -> None:
        return None

    def sim_set_pressure(self, pressure: float) -> None:
        self._state.line_pressure_psia = float(pressure)


class _SimAlicat:
    def __init__(self, state: _SimState) -> None:
        self._state = state
        self._error_model = None

    def read_status(self) -> AlicatReading:
        reported = self._reported_pressure_psia()
        mode = 'EXH' if self._state.exhaust else 'HLD'
        return AlicatReading(
            pressure=reported,
            setpoint=self._state.setpoint_psia,
            timestamp=time.time(),
            gauge_pressure=reported - self._state.barometric_psia,
            barometric_pressure=self._state.barometric_psia,
            raw_response=f'A +{reported:06.2f} +{self._state.setpoint_psia:06.2f} {mode}',
        )

    def _reported_pressure_psia(self) -> float:
        if self._state.vacuum_solenoid and self._state.line_pressure_psia < (
            self._state.barometric_psia - 1.0
        ):
            return self._state.barometric_psia
        return self._state.line_pressure_psia

    def configure_units_from_ptp(self, _units: str) -> None:
        return None

    def set_ramp_rate(self, _rate: float) -> bool:
        return True

    def cancel_hold(self) -> bool:
        self._state.exhaust = False
        return True

    def set_pressure(self, setpoint: float) -> bool:
        self._state.exhaust = False
        self._state.setpoint_psia = float(setpoint)
        return True

    def exhaust(self) -> bool:
        self._state.exhaust = True
        self._state.setpoint_psia = 0.0
        self._state.line_pressure_psia = self._state.barometric_psia
        return True

    def hold_valve(self) -> bool:
        return True

    def disconnect(self) -> None:
        return None


class SimulatedQualityPort:
    """Minimal Port stand-in that models vacuum-line Alicat bleed."""

    def __init__(self, *, barometric_psia: float = 14.7) -> None:
        self.port_id = PortId.PORT_A
        self._solenoid_config = {'safe_vacuum_switch_threshold_psi': 2.0}
        self._state = _SimState(
            barometric_psia=barometric_psia,
            line_pressure_psia=barometric_psia,
            setpoint_psia=barometric_psia,
        )
        self.daq = _SimDaq(self._state)
        self.alicat = _SimAlicat(self._state)

    @property
    def vent_count(self) -> int:
        return self._state.vent_count

    def read_all(self) -> PortReading:
        self._step_toward_setpoint()
        line = self._state.line_pressure_psia
        now = time.time()
        return PortReading(
            transducer=TransducerReading(
                voltage=0.0,
                pressure=line,
                pressure_raw=line,
                pressure_reference='absolute',
                timestamp=now,
            ),
            alicat=self.alicat.read_status(),
            timestamp=now,
        )

    def _step_toward_setpoint(self) -> None:
        if self._state.exhaust:
            self._state.line_pressure_psia = self._state.barometric_psia
            return
        target = self._state.setpoint_psia
        if self._state.vacuum_solenoid:
            step = 0.35
        else:
            step = 0.8
        delta = target - self._state.line_pressure_psia
        if abs(delta) <= step:
            self._state.line_pressure_psia = target
        else:
            self._state.line_pressure_psia += step if delta > 0 else -step

    def set_pressure(self, setpoint: float) -> bool:
        return self.alicat.set_pressure(setpoint)

    def set_solenoid(self, to_vacuum: bool) -> bool:
        """Replicate production pump protection (Alicat-only) for regression tests."""
        if to_vacuum:
            reading = self.alicat.read_status()
            pressure = reading.pressure
            if pressure is None:
                return False
            threshold = float(self._solenoid_config.get('safe_vacuum_switch_threshold_psi', 2.0))
            baro = self._state.barometric_psia
            if pressure > baro + threshold:
                return False
        return self.daq.set_solenoid(to_vacuum)

    def vent_to_atmosphere(self) -> bool:
        self._state.vent_count += 1
        self.daq.set_solenoid(False)
        return self.alicat.exhaust()
