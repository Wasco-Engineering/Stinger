"""Discover which DIO lines toggle per port during a vacuum pressure sweep.

Usage:
    python scripts/discover_switch_dio.py
    python scripts/discover_switch_dio.py --port port_b
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_port_config, load_config
from app.hardware.alicat import AlicatController
from app.hardware.labjack import LabJackController

PORT_A_DB9_TO_DIO = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 7, 9: 8}
PORT_B_DB9_TO_DIO = {1: 9, 2: 10, 3: 11, 4: 12, 5: 13, 6: 14, 7: 15, 8: 16, 9: 17}


def dio_label(port_key: str, dio: int) -> str:
    pin_map = PORT_A_DB9_TO_DIO if port_key == 'port_a' else PORT_B_DB9_TO_DIO
    for pin, mapped in pin_map.items():
        if mapped == dio:
            return f'DIO{dio} (DB9 pin {pin})'
    return f'DIO{dio}'


def read_port_a_dios(lj: LabJackController) -> Dict[int, int]:
    values: Dict[int, int] = {}
    for dio in range(0, 9):
        names = [f'DIO{dio}']
        raw = lj._read_names_with_retry(names)
        if raw and len(raw) >= 1:
            values[dio] = int(bool(raw[0]))
    return values


def read_port_b_dios(lj: LabJackController) -> Dict[int, int]:
    values: Dict[int, int] = {}
    for dio in range(9, 18):
        names = [f'DIO{dio}']
        raw = lj._read_names_with_retry(names)
        if raw and len(raw) >= 1:
            values[dio] = int(bool(raw[0]))
    return values


def active_low_closed(raw: int) -> bool:
    return raw == 0


def discover_port(
    port_key: str,
    *,
    start_psi: float,
    end_psi: float,
    rate_psi_s: float,
) -> None:
    cfg = load_config()
    pc = get_port_config(cfg, port_key)
    lj_cfg = {**cfg['hardware']['labjack'], **pc['labjack']}
    alicat_cfg = {**cfg['hardware']['alicat'], **pc['alicat']}

    print('\n' + '=' * 72)
    print(f'  SWITCH DISCOVERY — {port_key.upper()}')
    print(f'  Config NO=DIO{lj_cfg.get("switch_no_dio")} NC=DIO{lj_cfg.get("switch_nc_dio")} '
          f'COM=DIO{lj_cfg.get("switch_com_dio")} derived_nc={lj_cfg.get("switch_nc_derived_from_no")}')
    print('=' * 72)

    lj = LabJackController(lj_cfg)
    if not lj.configure():
        print(f'  LabJack configure failed: {lj._last_status}')
        return

    no_dio = lj_cfg.get('switch_no_dio')
    nc_dio = lj_cfg.get('switch_nc_dio')
    com_dio = lj_cfg.get('switch_com_dio')
    solenoid_dio = lj_cfg.get('solenoid_dio')
    lj.configure_di_pins(no_dio, nc_dio, com_dio, com_state=lj_cfg.get('switch_com_state', 0))
    lj.set_solenoid(to_vacuum=True)

    read_dios = read_port_a_dios if port_key == 'port_a' else read_port_b_dios
    dio_range = range(0, 9) if port_key == 'port_a' else range(9, 18)

    baseline = read_dios(lj)
    print('\n  Baseline (active-low CLOSED = 0):')
    for dio in dio_range:
        raw = baseline.get(dio, -1)
        if raw < 0:
            continue
        mark = ' *' if dio in (no_dio, nc_dio) else ''
        print(f'    {dio_label(port_key, dio)}: raw={raw} closed={active_low_closed(raw)}{mark}')

    alicat = AlicatController(alicat_cfg)
    if not alicat.connect():
        print(f'  Alicat connect failed: {alicat._last_status}')
        lj.cleanup()
        return

    transitions: List[Tuple[float, int, int, int, Optional[float]]] = []
    last: Dict[int, int] = dict(baseline)
    edge_logged: set[int] = set()

    try:
        alicat.cancel_hold()
        alicat.set_ramp_rate(0, time_unit='s')
        alicat.set_pressure(start_psi)
        time.sleep(4.0)
        alicat.set_ramp_rate(rate_psi_s, time_unit='s')
        alicat.set_pressure(end_psi)

        t0 = time.perf_counter()
        timeout = abs(start_psi - end_psi) / max(rate_psi_s, 0.1) + 45.0
        last_alicat_p: Optional[float] = None
        last_alicat_t = 0.0

        print(f'\n  Sweeping {start_psi:.1f} -> {end_psi:.1f} PSIG at {rate_psi_s:.1f} PSIG/s (vacuum route)...')

        while time.perf_counter() - t0 < timeout:
            now = time.perf_counter()
            if now - last_alicat_t >= 0.25:
                st = alicat.read_status()
                if st:
                    last_alicat_p = st.pressure
                last_alicat_t = now

            current = read_dios(lj)
            for dio, raw in current.items():
                prev = last.get(dio)
                if prev is not None and raw != prev and dio not in edge_logged:
                    edge_logged.add(dio)
                    transducer = lj.read_transducer()
                    tr_p = transducer.pressure if transducer else None
                    transitions.append((now - t0, dio, prev, raw, last_alicat_p))
                    print(
                        f'  [EDGE] {dio_label(port_key, dio)}: {prev}->{raw} '
                        f'closed={active_low_closed(raw)} '
                        f'@ t={now - t0:.1f}s alicat={last_alicat_p:.2f} trans={tr_p}'
                        if last_alicat_p is not None and tr_p is not None
                        else f'  [EDGE] {dio_label(port_key, dio)}: {prev}->{raw} @ t={now - t0:.1f}s'
                    )
            last.update(current)

            if last_alicat_p is not None and last_alicat_p <= end_psi + 1.5:
                time.sleep(2.0)
                break
            time.sleep(0.02)

        # Return sweep
        print(f'\n  Returning toward {start_psi:.1f} PSIG...')
        alicat.set_pressure(start_psi)
        t1 = time.perf_counter()
        while time.perf_counter() - t1 < timeout:
            now = time.perf_counter()
            if now - last_alicat_t >= 0.25:
                st = alicat.read_status()
                if st:
                    last_alicat_p = st.pressure
                last_alicat_t = now
            current = read_dios(lj)
            for dio, raw in current.items():
                prev = last.get(dio)
                if prev is not None and raw != prev:
                    transducer = lj.read_transducer()
                    tr_p = transducer.pressure if transducer else None
                    transitions.append((now - t0, dio, prev, raw, last_alicat_p))
                    print(
                        f'  [EDGE] {dio_label(port_key, dio)}: {prev}->{raw} '
                        f'(return) alicat={last_alicat_p:.2f} trans={tr_p}'
                        if last_alicat_p is not None and tr_p is not None
                        else f'  [EDGE] {dio_label(port_key, dio)}: {prev}->{raw} (return)'
                    )
                last[dio] = raw
            if last_alicat_p is not None and last_alicat_p >= start_psi - 2.0:
                break
            time.sleep(0.02)

    finally:
        alicat.exhaust()
        alicat.disconnect()
        lj.set_solenoid_safe()
        lj.cleanup()

    com = int(com_dio) if com_dio is not None else -1
    toggled = sorted({dio for _, dio, _, _, _ in transitions if dio != com})
    print('\n  Summary — DIO lines that toggled (COM excluded):')
    if not toggled:
        print('    (none — check COM drive, switch wiring, or sweep range)')
    for dio in toggled:
        cfg_role = []
        if dio == no_dio:
            cfg_role.append('configured NO')
        if dio == nc_dio:
            cfg_role.append('configured NC')
        role = f" [{', '.join(cfg_role)}]" if cfg_role else ' [not in config]'
        print(f'    {dio_label(port_key, dio)}{role}')

    switch_lines = [d for d in toggled if d in (no_dio, nc_dio)]
    if nc_dio in switch_lines and no_dio in switch_lines:
        print('\n  Both NO and NC DIO lines toggled — use separate pins in config.')
    elif no_dio in toggled and nc_dio not in toggled:
        print(
            f'\n  Only NO line (DIO{no_dio}) toggled; configured NC (DIO{nc_dio}) did not. '
            'Single-pole harness — use switch_nc_derived_from_no: true.'
        )
    elif not toggled:
        print('\n  No switch lines toggled during sweep.')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', choices=['port_a', 'port_b', 'both'], default='both')
    parser.add_argument('--start-psi', type=float, default=14.7)
    parser.add_argument('--end-psi', type=float, default=0.5)
    parser.add_argument('--rate', type=float, default=2.0)
    args = parser.parse_args()

    ports = ['port_a', 'port_b'] if args.port == 'both' else [args.port]
    for pk in ports:
        discover_port(pk, start_psi=args.start_psi, end_psi=args.end_psi, rate_psi_s=args.rate)


if __name__ == '__main__':
    main()
