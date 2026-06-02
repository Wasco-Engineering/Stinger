#!/usr/bin/env python3
"""Hold both ports on vacuum for pump/fitting/hermetic checks.

Routes both solenoids to vacuum, commands low Alicat setpoints, and prints
live pressure until you press Ctrl+C or --duration elapses.

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\vacuum_hermetic_hold.py
    .\\.venv\\Scripts\\python.exe scripts\\vacuum_hermetic_hold.py --duration 120
    .\\.venv\\Scripts\\python.exe scripts\\vacuum_hermetic_hold.py --port port_b
"""

from __future__ import annotations

import argparse
import signal
import statistics
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_config
from app.hardware.port import PortManager

SAFE_SETPOINT_PSIA = 0.2
DEFAULT_HOLD_SETPOINT_PSIA = 0.0


def _baro(reading) -> float:
    if reading and reading.alicat and reading.alicat.barometric_pressure is not None:
        return float(reading.alicat.barometric_pressure)
    return 14.7


def _transducer_psi(reading, baro: float) -> Optional[float]:
    if not reading or not reading.transducer:
        return None
    p = reading.transducer.pressure
    ref = str(reading.transducer.pressure_reference or 'absolute').lower()
    if ref == 'gauge':
        return p + baro
    return p


def _alicat_psi(reading) -> Optional[float]:
    if reading and reading.alicat and reading.alicat.pressure is not None:
        return float(reading.alicat.pressure)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description='Hold system on vacuum for leak/hermetic test.')
    parser.add_argument(
        '--ports',
        default='port_a,port_b',
        help='Comma-separated ports (default: both).',
    )
    parser.add_argument(
        '--setpoint-psia',
        type=float,
        default=DEFAULT_HOLD_SETPOINT_PSIA,
        help='Alicat absolute setpoint while on vacuum (default 0).',
    )
    parser.add_argument(
        '--duration',
        type=float,
        default=0.0,
        help='Seconds to hold (0 = until Ctrl+C).',
    )
    parser.add_argument('--interval', type=float, default=1.0, help='Sample interval seconds.')
    args = parser.parse_args()

    port_keys = [p.strip() for p in args.ports.split(',') if p.strip()]
    stop = False

    def _stop(*_args) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, _stop)

    config = load_config()
    manager = PortManager(config)
    manager.initialize_ports()
    if not manager.connect_all():
        print('ERROR: Could not connect all ports. Check LabJack and Alicat.')
        return 1

    ports = []
    for key in port_keys:
        port = manager.get_port(key)
        if port is None:
            print(f'ERROR: Unknown port {key}')
            return 1
        ports.append(port)

    try:
        print('Venting to atmosphere / safe setpoint before vacuum routing...', flush=True)
        for port in ports:
            port.vent_to_atmosphere()
            port.set_pressure(SAFE_SETPOINT_PSIA)
            time.sleep(1.0)

        print('Waiting for pressures near atmosphere (up to 30 s)...')
        for _ in range(30):
            ok = True
            for port in ports:
                r = port.read_all()
                b = _baro(r)
                ap = _alicat_psi(r)
                if ap is not None and ap > b + 2.5:
                    ok = False
            if ok:
                break
            time.sleep(1.0)
        else:
            print('WARNING: Pressure may still be above safe vacuum threshold; vacuum may be refused.')

        print(f'Commanding setpoint {args.setpoint_psia:.2f} PSIA and solenoid VACUUM on: {port_keys}')
        for port in ports:
            port.alicat.cancel_hold()
            port.set_pressure(args.setpoint_psia)

        time.sleep(0.5)
        for port in ports:
            label = port.port_id.value
            if not port.set_solenoid(True):
                print(
                    f'ERROR: {label} refused vacuum (pressure too high?). '
                    'Run vent_to_atmosphere or lower pressure first.',
                )
                return 1
            print(f'  {label}: solenoid -> VACUUM OK')

        print('\nOn VACUUM — listen for pump, watch for leaks. Ctrl+C to stop.\n')
        print(f'{"time":>6}  {"port":<8}  {"alicat":>8}  {"transducer":>10}  {"note"}')
        print('-' * 52)

        t0 = time.perf_counter()
        history: dict[str, list[float]] = {p.port_id.value: [] for p in ports}

        while not stop:
            elapsed = time.perf_counter() - t0
            if args.duration > 0 and elapsed >= args.duration:
                break
            for port in ports:
                r = port.read_all()
                b = _baro(r)
                ap = _alicat_psi(r)
                tp = _transducer_psi(r, b)
                key = port.port_id.value
                if ap is not None:
                    history[key].append(ap)
                note = ''
                if len(history[key]) >= 10:
                    recent = history[key][-10:]
                    rate = (recent[-1] - recent[0]) / max(len(recent) - 1, 1) / args.interval
                    if abs(rate) > 0.05:
                        note = f'~{rate * 60:.2f} psi/min'
                print(
                    f'{elapsed:6.1f}  {key:<8}  '
                    f'{ap if ap is not None else float("nan"):8.2f}  '
                    f'{tp if tp is not None else float("nan"):10.2f}  {note}',
                    flush=True,
                )
            time.sleep(max(0.2, args.interval))

        print('\nRestoring atmosphere on all ports...')
    finally:
        for port in ports:
            try:
                port.vent_to_atmosphere()
                port.set_pressure(SAFE_SETPOINT_PSIA)
            except Exception as exc:
                print(f'Cleanup warning {port.port_id.value}: {exc}')
        manager.disconnect_all()

    print('Done — system returned to atmosphere route.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
