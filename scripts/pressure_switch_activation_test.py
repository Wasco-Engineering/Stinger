"""
Find pressure-switch activation on port_a by sweeping Alicat pressure and
monitoring configured and discovered DIO mappings.

Usage:
    python scripts/pressure_switch_activation_test.py --port port_a --sweep-vacuum --start-psi 14 --end-psi 0.5
    python scripts/pressure_switch_activation_test.py --mapping spec_a_no2_nc0_com3
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_port_config, load_config
from app.hardware.alicat import AlicatController
from app.hardware.labjack import LabJackController, SwitchState

# Candidate mappings: (name, no_dio, nc_dio, com_dio, com_state)
MAPPINGS = {
    # From docs/HARDWARE_SPEC.md (left / port_a)
    'spec_a_no2_nc0_com3': (2, 0, 3, 0),
    'spec_a_swap_no_nc': (0, 2, 3, 0),
    'spec_a_com_high': (2, 0, 3, 1),
    # Right-port DB9 lines (port_b harness — may appear on left connector)
    'spec_b_no9_nc11_com12': (9, 11, 12, 0),
    'spec_b_swap_no_nc': (11, 9, 12, 0),
    # COM-drive discoveries (DIO2/3 pair responsive)
    'dio2_3_com2': (2, 3, 2, 0),
    'dio3_2_com2': (3, 2, 2, 0),
    'dio2_3_com3': (2, 3, 3, 0),
    # Other DB9 permutations on port A FIO block
    'dio0_1_com3': (0, 1, 3, 0),
    'dio1_0_com3': (1, 0, 3, 0),
    'dio8_10_com12': (8, 10, 12, 0),
}


@dataclass
class EdgeEvent:
    direction: str
    pressure_psi: float
    alicat_psi: float
    switch: SwitchState


def _read_switch(
    labjack: LabJackController,
    *,
    no_dio: int,
    nc_dio: int,
    active_low: bool,
) -> Optional[SwitchState]:
    """Read NO/NC using explicit DIO assignment (temporarily override config)."""
    if not labjack.hardware_available():
        return None
    handle = labjack._shared_handle
    if handle is None:
        return None
    from labjack import ljm

    try:
        states = ljm.eReadNames(handle, 2, [f'DIO{no_dio}', f'DIO{nc_dio}'])
        no_raw = bool(states[0])
        nc_raw = bool(states[1])
        if active_low:
            no_active = not no_raw
            nc_active = not nc_raw
        else:
            no_active = no_raw
            nc_active = nc_raw
        return SwitchState(no_active=no_active, nc_active=nc_active, timestamp=time.time())
    except Exception:
        return None


def _run_sweep(
    *,
    mapping_name: str,
    no_dio: int,
    nc_dio: int,
    com_dio: int,
    com_state: int,
    labjack: LabJackController,
    alicat: AlicatController,
    start_psi: float,
    end_psi: float,
    rate_psi_s: float,
    active_low: bool,
    to_vacuum: bool = False,
) -> tuple[list[EdgeEvent], list[dict]]:
    labjack.configure_di_pins(
        no_pin=no_dio,
        nc_pin=nc_dio,
        com_pin=com_dio,
        com_state=com_state,
    )
    labjack.set_solenoid(to_vacuum=to_vacuum)

    edges: list[EdgeEvent] = []
    rows: list[dict] = []
    last_activated: Optional[bool] = None

    alicat.cancel_hold()
    time.sleep(0.1)
    alicat.set_ramp_rate(0, time_unit='s')
    time.sleep(0.1)
    alicat.set_pressure(start_psi)
    time.sleep(6.0)

    sw = _read_switch(labjack, no_dio=no_dio, nc_dio=nc_dio, active_low=active_low)
    trans = labjack.read_transducer()
    status = alicat.read_status()
    if sw:
        last_activated = sw.switch_activated
        print(
            f'  [{mapping_name}] start P={status.pressure:.2f} '
            f'NO={sw.no_active} NC={sw.nc_active} valid={sw.is_valid} act={sw.switch_activated}'
        )

    alicat.set_ramp_rate(rate_psi_s, time_unit='s')
    time.sleep(0.1)
    alicat.set_pressure(end_psi)

    timeout = abs(end_psi - start_psi) / max(rate_psi_s, 0.1) + 20.0
    t0 = time.perf_counter()
    last_log = 0.0

    while time.perf_counter() - t0 < timeout:
        now = time.perf_counter()
        status = alicat.read_status()
        sw = _read_switch(labjack, no_dio=no_dio, nc_dio=nc_dio, active_low=active_low)
        trans = labjack.read_transducer()
        alicat_p = status.pressure if status else float('nan')
        trans_p = trans.pressure if trans else float('nan')

        if sw and last_activated is not None and sw.switch_activated != last_activated:
            direction = 'ACTIVATED' if sw.switch_activated else 'DEACTIVATED'
            edges.append(
                EdgeEvent(direction, trans_p, alicat_p, sw),
            )
            print(
                f'  [{mapping_name}] EDGE {direction} @ trans={trans_p:.2f} alicat={alicat_p:.2f} '
                f'NO={sw.no_active} NC={sw.nc_active}'
            )

        if sw:
            last_activated = sw.switch_activated

        if now - last_log >= 0.25:
            rows.append(
                {
                    'mapping': mapping_name,
                    'elapsed_s': now - t0,
                    'alicat_psi': alicat_p,
                    'trans_psi': trans_p,
                    'no_active': sw.no_active if sw else None,
                    'nc_active': sw.nc_active if sw else None,
                    'valid': sw.is_valid if sw else None,
                    'activated': sw.switch_activated if sw else None,
                }
            )
            last_log = now

        if status and abs(status.pressure - end_psi) < 0.8:
            break
        time.sleep(0.02)

    time.sleep(2.0)
    # Return sweep
    alicat.set_ramp_rate(rate_psi_s, time_unit='s')
    alicat.set_pressure(start_psi)
    t1 = time.perf_counter()
    while time.perf_counter() - t1 < timeout:
        status = alicat.read_status()
        sw = _read_switch(labjack, no_dio=no_dio, nc_dio=nc_dio, active_low=active_low)
        trans = labjack.read_transducer()
        if sw and last_activated is not None and sw.switch_activated != last_activated:
            direction = 'ACTIVATED' if sw.switch_activated else 'DEACTIVATED'
            edges.append(
                EdgeEvent(direction, trans.pressure if trans else 0.0, status.pressure if status else 0.0, sw),
            )
            print(
                f'  [{mapping_name}] EDGE {direction} (return) @ '
                f'trans={trans.pressure if trans else 0:.2f} alicat={status.pressure if status else 0:.2f}'
            )
        if sw:
            last_activated = sw.switch_activated
        if status and abs(status.pressure - start_psi) < 1.0:
            break
        time.sleep(0.02)

    return edges, rows


def main() -> int:
    parser = argparse.ArgumentParser(description='Pressure switch activation finder')
    parser.add_argument('--port', choices=['port_a', 'port_b'], default='port_a')
    parser.add_argument('--start-psi', type=float, default=8.0)
    parser.add_argument('--end-psi', type=float, default=28.0)
    parser.add_argument('--rate', type=float, default=2.0)
    parser.add_argument(
        '--mapping',
        choices=list(MAPPINGS.keys()) + ['all'],
        default='all',
    )
    parser.add_argument(
        '--polarity',
        choices=['config', 'true', 'false', 'both'],
        default='both',
        help='switch_active_low for reads (both tries true and false)',
    )
    parser.add_argument(
        '--sweep-vacuum',
        action='store_true',
        help='Route exhaust to vacuum pump (required for vacuum switch tests)',
    )
    args = parser.parse_args()

    config = load_config()
    port_key = args.port
    port_cfg = get_port_config(config, port_key)
    lj_base = config['hardware']['labjack']
    lj_port = port_cfg['labjack']
    lj_cfg = {**lj_base, **lj_port}
    alicat_cfg = {**config['hardware']['alicat'], **port_cfg['alicat']}
    config_active_low = bool(lj_port.get('switch_active_low', True))
    if args.polarity == 'config':
        polarities = [config_active_low]
    elif args.polarity == 'both':
        polarities = [True, False]
    else:
        polarities = [args.polarity == 'true']

    labjack = LabJackController(lj_cfg)
    if not labjack.configure():
        print(f'LabJack configure failed: {labjack._last_status}')
        return 1

    alicat = AlicatController(alicat_cfg)
    if not alicat.connect():
        print(f'Alicat connect failed: {alicat._last_status}')
        labjack.cleanup()
        return 1

    mappings = MAPPINGS if args.mapping == 'all' else {args.mapping: MAPPINGS[args.mapping]}
    all_rows: list[dict] = []
    results: dict[str, list[EdgeEvent]] = {}

    print(f'Pressure switch activation test — {port_key}')
    print(f'Sweep {args.start_psi} -> {args.end_psi} PSI @ {args.rate} PSI/s')
    print(f'Solenoid route: {"VACUUM" if args.sweep_vacuum else "ATMOSPHERE"}')
    print(f'Polarities to try: {polarities}\n')

    try:
        for active_low in polarities:
            pol_tag = 'active_low' if active_low else 'active_high'
            for name, (no_dio, nc_dio, com_dio, com_state) in mappings.items():
                run_key = f'{name}_{pol_tag}'
                print(
                    f'--- {run_key}: NO={no_dio} NC={nc_dio} COM={com_dio} '
                    f'com_state={com_state} ---'
                )
                edges, rows = _run_sweep(
                    mapping_name=run_key,
                    no_dio=no_dio,
                    nc_dio=nc_dio,
                    com_dio=com_dio,
                    com_state=com_state,
                    labjack=labjack,
                    alicat=alicat,
                    start_psi=args.start_psi,
                    end_psi=args.end_psi,
                    rate_psi_s=args.rate,
                    active_low=active_low,
                    to_vacuum=args.sweep_vacuum,
                )
                results[run_key] = edges
                all_rows.extend(rows)
                alicat.set_ramp_rate(0, time_unit='s')
                alicat.set_pressure(14.7)
                time.sleep(3.0)
    finally:
        try:
            alicat.set_pressure(14.7)
            alicat.disconnect()
        except Exception:
            pass
        labjack.set_solenoid_safe()
        labjack.cleanup()

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = PROJECT_ROOT / 'scripts' / 'data' / f'pressure_switch_activation_{ts}.csv'
    out.parent.mkdir(parents=True, exist_ok=True)
    if all_rows:
        with out.open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            writer.writeheader()
            writer.writerows(all_rows)
        print(f'\nLog: {out}')

    print('\n=== SUMMARY ===')
    passed = False
    for name, edges in results.items():
        n = len(edges)
        status = 'PASS' if n >= 1 else 'FAIL'
        if n >= 1:
            passed = True
        print(f'  {name}: {status} ({n} edge(s))')
        for e in edges:
            print(f'    {e.direction} trans={e.pressure_psi:.2f} alicat={e.alicat_psi:.2f}')

    if passed:
        print('\nRecommend updating stinger_config.yaml port_a switch_no_dio / switch_nc_dio / switch_com_dio')
        print('to the mapping that showed edges.')
        return 0
    print('\nNo edges on any candidate mapping — check harness, COM drive, and sweep range.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
