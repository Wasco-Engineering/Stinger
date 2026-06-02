"""Reusable pressure reading/sample builders for tests."""

from __future__ import annotations

from app.hardware.alicat import AlicatReading
from app.hardware.labjack import TransducerReading
from app.hardware.port import PortReading
from app.services.pressure_calibration import CalibrationSample


def build_port_reading(
    *,
    timestamp: float = 1.0,
    transducer_pressure: float = 10.0,
    transducer_reference: str = 'absolute',
    alicat_pressure: float = 10.0,
    alicat_setpoint: float = 10.0,
    barometric_pressure: float = 14.7,
    gauge_pressure: float | None = None,
) -> PortReading:
    return PortReading(
        transducer=TransducerReading(
            voltage=2.5,
            pressure=transducer_pressure,
            pressure_raw=transducer_pressure,
            pressure_reference=transducer_reference,
            timestamp=timestamp,
        ),
        alicat=AlicatReading(
            pressure=alicat_pressure,
            setpoint=alicat_setpoint,
            timestamp=timestamp,
            gauge_pressure=gauge_pressure,
            barometric_pressure=barometric_pressure,
        ),
        timestamp=timestamp,
    )


def calibration_sample(
    idx: int,
    *,
    phase: str = 'static_10',
    target: float = 10.0,
    transducer: float = 10.0,
    alicat: float = 10.0,
    mensor: float | None = None,
) -> CalibrationSample:
    return CalibrationSample(
        index=idx,
        timestamp=float(idx),
        port_id='port_a',
        phase=phase,
        target_abs_psi=target,
        transducer_abs_psi=transducer,
        alicat_abs_psi=alicat,
        mensor_abs_psia=mensor if mensor is not None else alicat,
    )
