"""Read-only PTP switch diagnostic for a part/sequence/port."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_config
from app.database.session import close_database, get_engine, initialize_database
from app.hardware.labjack import LabJackController, SwitchState
from app.services.ptp_service import (
    derive_test_setup,
    load_ptp_from_db,
    load_ptp_from_dump,
    validate_ptp_params,
)
from app.services.ptp_switch_resolver import PtpSwitchResolution, resolve_ptp_switch_config


def resolve_for_diagnostic(
    *,
    part_id: str,
    sequence_id: str,
    port_id: str,
    config: dict[str, Any],
) -> tuple[dict[str, str], PtpSwitchResolution]:
    params = load_ptp_from_db(part_id, sequence_id) if get_engine() is not None else {}
    if not params:
        params = load_ptp_from_dump(part_id, sequence_id)
    if not params:
        raise RuntimeError(f'No PTP parameters found for {part_id}/{sequence_id}')

    is_valid, errors = validate_ptp_params(params)
    if not is_valid:
        raise RuntimeError('PTP validation failed: ' + '; '.join(errors))

    port_config = _merged_labjack_config(config, port_id)
    resolution = resolve_ptp_switch_config(
        ptp_params=params,
        port_id=port_id,
        port_config=port_config,
    )
    return params, resolution


def configure_labjack_for_resolution(
    labjack: LabJackController,
    resolution: PtpSwitchResolution,
    *,
    com_state: int,
) -> None:
    if not resolution.is_valid:
        raise RuntimeError('Cannot configure LabJack from invalid switch resolution')
    labjack.switch_nc_derived_from_no = resolution.derive_nc_from_no
    labjack.switch_no_derived_from_nc = resolution.derive_no_from_nc
    labjack.configure_di_pins(
        resolution.no_dio,
        resolution.nc_dio,
        resolution.common_dio,
        com_state=com_state,
    )


def format_switch_state(state: Optional[SwitchState]) -> str:
    if state is None:
        return 'switch_state=unavailable'
    return (
        f'no_active={state.no_active} '
        f'nc_active={state.nc_active} '
        f'switch_activated={state.switch_activated} '
        f'valid={state.is_valid}'
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--part', required=True)
    parser.add_argument('--sequence', required=True)
    parser.add_argument('--port', choices=('port_a', 'port_b'), required=True)
    parser.add_argument('--duration-s', type=float, default=10.0)
    parser.add_argument('--interval-s', type=float, default=0.25)
    args = parser.parse_args(argv)

    config = load_config()
    db_initialized = initialize_database(config.get('database', {}))
    try:
        params, resolution = resolve_for_diagnostic(
            part_id=args.part,
            sequence_id=args.sequence,
            port_id=args.port,
            config=config,
        )
        setup = derive_test_setup(args.part, args.sequence, params)
        print(f'Application: {setup.part_id}/{setup.sequence_id}')
        print(
            f'PTP: units={setup.units_label} reference={setup.pressure_reference} '
            f'direction={setup.activation_direction}'
        )
        print(f'Resolved switch: {resolution.summary}')
        if resolution.warnings:
            print('Warnings: ' + '; '.join(resolution.warnings))
        if not resolution.is_valid:
            print('ERROR: ' + '; '.join(resolution.errors))
            return 2

        lj_config = _merged_labjack_config(config, args.port)
        labjack = LabJackController(lj_config)
        if not labjack.configure():
            print(f'ERROR: LabJack configure failed: {labjack._last_status}')
            return 3
        configure_labjack_for_resolution(
            labjack,
            resolution,
            com_state=int(lj_config.get('switch_com_state', 0)),
        )

        print('Polling switch state. Move/tap the switch if needed; no DB writes are performed.')
        deadline = time.monotonic() + max(args.duration_s, 0.0)
        while time.monotonic() <= deadline:
            dio = labjack.read_dio_values(max_dio=22) or {}
            state = labjack.read_switch_state()
            raw = _format_raw_dio(dio, resolution)
            print(f'{time.strftime("%H:%M:%S")} {raw} {format_switch_state(state)}')
            time.sleep(max(args.interval_s, 0.05))
        labjack.cleanup()
        return 0
    finally:
        if db_initialized:
            close_database()


def _merged_labjack_config(config: dict[str, Any], port_id: str) -> dict[str, Any]:
    labjack = config.get('hardware', {}).get('labjack', {})
    base = {key: value for key, value in labjack.items() if key not in {'port_a', 'port_b'}}
    return {**base, **labjack.get(port_id, {})}


def _format_raw_dio(dio: dict[int, int], resolution: PtpSwitchResolution) -> str:
    pins = [
        ('COM', resolution.common_dio),
        ('NO', resolution.no_dio),
        ('NC', resolution.nc_dio),
    ]
    return ' '.join(
        f'{label}=DIO{pin}:{dio.get(pin, "?")}'
        for label, pin in pins
        if pin is not None
    )


if __name__ == '__main__':
    raise SystemExit(main())
