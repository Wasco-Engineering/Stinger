#!/usr/bin/env python3
"""Vacuum pull for leak-down test (pump off, re-check later).

  pull  — Evacuate both ports, save baseline pressures, leave solenoids on VACUUM,
          hold Alicat valves, exit without venting. Turn the pump OFF after pull.

  check   — Read current pressures and compare to the saved baseline.
  hold    — Pull vacuum until stable, then timestamped log (recommended for 1 hr test).
  monitor — Read-only log only (does NOT pull vacuum; use hold instead).

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\vacuum_leak_test.py hold --duration 3600 --interval 60
    .\\.venv\\Scripts\\python.exe scripts\\vacuum_leak_test.py pull
    .\\.venv\\Scripts\\python.exe scripts\\vacuum_leak_test.py check
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_config
from app.hardware.port import PortManager

STATE_PATH = PROJECT_ROOT / 'logs' / 'vacuum_leak_state.json'
SAFE_SETPOINT_PSIA = 0.2
PULL_SETPOINT_PSIA = 0.0
STABLE_MAX_PSIA = 0.15
STABLE_SECONDS = 8.0


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
        return float(p) + baro
    return float(p)


def _alicat_psi(reading) -> Optional[float]:
    if reading and reading.alicat and reading.alicat.pressure is not None:
        return float(reading.alicat.pressure)
    return None


def _read_port(port) -> Dict[str, Optional[float]]:
    r = port.read_all()
    b = _baro(r)
    return {
        'alicat_psia': _alicat_psi(r),
        'transducer_psia': _transducer_psi(r, b),
        'barometric_psia': b,
    }


def _wait_near_atmosphere(ports, timeout_s: float = 30.0) -> bool:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        ok = True
        for port in ports:
            ap = _read_port(port).get('alicat_psia')
            b = _read_port(port).get('barometric_psia') or 14.7
            if ap is not None and ap > float(b) + 2.5:
                ok = False
        if ok:
            return True
        time.sleep(1.0)
    return False


def _wait_stable_vacuum(ports, max_psia: float, hold_s: float) -> bool:
    """Return True when all ports stay below max_psia for hold_s."""
    stable_since: Optional[float] = None
    deadline = time.perf_counter() + 120.0
    while time.perf_counter() < deadline:
        samples = [_read_port(p) for p in ports]
        pressures = [
            s.get('alicat_psia') or s.get('transducer_psia')
            for s in samples
        ]
        if any(p is None for p in pressures):
            time.sleep(0.5)
            continue
        if all(float(p) <= max_psia for p in pressures):
            if stable_since is None:
                stable_since = time.perf_counter()
            elif time.perf_counter() - stable_since >= hold_s:
                return True
        else:
            stable_since = None
        for port, s in zip(ports, samples):
            key = port.port_id.value
            ap = s.get('alicat_psia')
            tp = s.get('transducer_psia')
            print(
                f'  {key}: alicat={ap:.3f} transducer={tp:.3f} psia',
                flush=True,
            )
        time.sleep(1.0)
    return False


def _pull_vacuum(ports) -> tuple[Dict[str, Any], bool]:
    """Evacuate both ports and return (snapshot, success)."""
    print('Step 1: Vent / safe setpoint...', flush=True)
    for port in ports:
        port.vent_to_atmosphere()
        port.set_pressure(SAFE_SETPOINT_PSIA)
    time.sleep(2.0)

    if not _wait_near_atmosphere(ports):
        print('WARNING: Pressure still above safe vacuum threshold.', flush=True)

    print(f'Step 2: Pull vacuum (setpoint {PULL_SETPOINT_PSIA:.2f} PSIA)...', flush=True)
    for port in ports:
        port.alicat.cancel_hold()
        port.set_pressure(PULL_SETPOINT_PSIA)
    time.sleep(0.5)

    for port in ports:
        if not port.set_solenoid(True):
            print(f'ERROR: {port.port_id.value} refused vacuum route.', flush=True)
            return {}, False
        print(f'  {port.port_id.value}: VACUUM route ON', flush=True)

    print('Step 3: Wait for stable low pressure...', flush=True)
    if not _wait_stable_vacuum(ports, STABLE_MAX_PSIA, STABLE_SECONDS):
        print('WARNING: Did not reach stable vacuum in time; saving snapshot anyway.', flush=True)

    snapshot: Dict[str, Any] = {}
    for port in ports:
        snapshot[port.port_id.value] = _read_port(port)
    return snapshot, True


def _save_baseline(snapshot: Dict[str, Any]) -> None:
    state = {
        'pulled_at': datetime.now(timezone.utc).isoformat(),
        'pull_setpoint_psia': PULL_SETPOINT_PSIA,
        'stable_max_psia': STABLE_MAX_PSIA,
        'ports': snapshot,
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding='utf-8')
    print('\n--- BASELINE SAVED ---', flush=True)
    for key, vals in snapshot.items():
        print(
            f'  {key}: alicat={vals.get("alicat_psia")} transducer={vals.get("transducer_psia")} PSIA',
            flush=True,
        )
    print(f'\nState file: {STATE_PATH}', flush=True)


def _print_monitor_header() -> None:
    print(
        f'\n{"timestamp_utc":<26}  {"port_a_alicat":>12}  {"port_a_trans":>12}  '
        f'{"port_b_alicat":>12}  {"port_b_trans":>12}',
        flush=True,
    )
    print('-' * 78, flush=True)


def _print_monitor_row(ports, log_path: Optional[Path] = None) -> None:
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    row: Dict[str, Optional[float]] = {}
    for port in ports:
        key = port.port_id.value
        vals = _read_port(port)
        row[f'{key}_alicat'] = vals.get('alicat_psia')
        row[f'{key}_trans'] = vals.get('transducer_psia')
        if log_path:
            with log_path.open('a', encoding='utf-8') as fh:
                fh.write(f'{ts},{key},{vals.get("alicat_psia")},{vals.get("transducer_psia")}\n')

    def cell(prefix: str, field: str) -> str:
        v = row.get(f'{prefix}_{field}')
        return f'{v:12.3f}' if v is not None else f'{"n/a":>12}'

    print(
        f'{ts:<26}  {cell("port_a", "alicat")}  {cell("port_a", "trans")}  '
        f'{cell("port_b", "alicat")}  {cell("port_b", "trans")}',
        flush=True,
    )


def cmd_pull(args: argparse.Namespace) -> int:
    config = load_config()
    manager = PortManager(config)
    manager.initialize_ports()
    if not manager.connect_all():
        print('ERROR: Could not connect hardware.', flush=True)
        return 1

    ports = [manager.get_port(k) for k in ('port_a', 'port_b')]
    ports = [p for p in ports if p is not None]

    try:
        snapshot, ok = _pull_vacuum(ports)
        if not ok:
            manager.disconnect_all(restore_safe_state=True)
            return 1
        _save_baseline(snapshot)
        print('\n>>> TURN THE VACUUM PUMP OFF NOW <<<', flush=True)
        print('Then wait (e.g. 1 hour) and run:', flush=True)
        print('  .\\.venv\\Scripts\\python.exe scripts\\vacuum_leak_test.py check\n', flush=True)
        print('Holding Alicat valves; disconnecting (solenoids stay on VACUUM)...', flush=True)
        for port in ports:
            try:
                port.alicat.hold_valve()
            except Exception:
                pass
    finally:
        manager.disconnect_all(restore_safe_state=False)

    print('Done. Hardware left on vacuum route for leak-down.', flush=True)
    return 0


def cmd_hold(args: argparse.Namespace) -> int:
    """Pull vacuum, then log pressures without disconnecting (keeps vacuum route)."""
    config = load_config()
    manager = PortManager(config)
    manager.initialize_ports()
    if not manager.connect_all():
        print('ERROR: Could not connect hardware.', flush=True)
        return 1

    ports = [manager.get_port(k) for k in ('port_a', 'port_b')]
    ports = [p for p in ports if p is not None]
    log_path: Optional[Path] = Path(args.log) if args.log else None
    if log_path and not log_path.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text('timestamp_utc,port,alicat_psia,transducer_psia\n', encoding='utf-8')

    restore_on_exit = bool(args.restore)
    t0 = time.perf_counter()
    try:
        snapshot, ok = _pull_vacuum(ports)
        if not ok:
            manager.disconnect_all(restore_safe_state=True)
            return 1
        _save_baseline(snapshot)

        for port in ports:
            try:
                port.alicat.hold_valve()
            except Exception:
                pass

        print('\n>>> VACUUM STABLE — TURN THE VACUUM PUMP OFF NOW <<<', flush=True)
        print(
            f'Logging every {args.interval:.0f}s for '
            f'{"until Ctrl+C" if args.duration <= 0 else f"{args.duration:.0f}s"} '
            '(stays on vacuum; Ctrl+C to stop early).\n',
            flush=True,
        )
        _print_monitor_header()
        _print_monitor_row(ports, log_path)

        while True:
            time.sleep(max(1.0, args.interval))
            _print_monitor_row(ports, log_path)
            if args.duration > 0 and (time.perf_counter() - t0) >= args.duration:
                break
    except KeyboardInterrupt:
        print('\nStopped early.', flush=True)
    finally:
        if restore_on_exit:
            print('\nRestoring atmosphere...', flush=True)
            for port in ports:
                port.vent_to_atmosphere()
                port.set_pressure(SAFE_SETPOINT_PSIA)
        manager.disconnect_all(restore_safe_state=restore_on_exit)

    if log_path:
        print(f'Log: {log_path}', flush=True)
    if not restore_on_exit:
        print('Left on VACUUM route. Run check later or: vacuum_leak_test.py restore', flush=True)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    if not STATE_PATH.exists():
        print(f'No baseline found at {STATE_PATH}. Run pull first.', flush=True)
        return 1

    state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    pulled_at = state.get('pulled_at', 'unknown')
    baseline = state.get('ports', {})

    config = load_config()
    manager = PortManager(config)
    manager.initialize_ports()
    if not manager.connect_all():
        print('ERROR: Could not connect hardware.', flush=True)
        return 1

    try:
        print(f'Baseline from: {pulled_at}', flush=True)
        print(f'Checked at:  {datetime.now(timezone.utc).isoformat()}\n', flush=True)
        print(f'{"port":<8}  {"alicat_was":>10}  {"alicat_now":>10}  {"d_alicat":>10}  '
              f'{"trans_was":>10}  {"trans_now":>10}  {"d_trans":>10}', flush=True)
        print('-' * 78, flush=True)

        for key in ('port_a', 'port_b'):
            port = manager.get_port(key)
            if port is None:
                continue
            now = _read_port(port)
            was = baseline.get(key, {})
            a_was = was.get('alicat_psia')
            a_now = now.get('alicat_psia')
            t_was = was.get('transducer_psia')
            t_now = now.get('transducer_psia')
            d_a = (a_now - a_was) if a_was is not None and a_now is not None else None
            d_t = (t_now - t_was) if t_was is not None and t_now is not None else None

            def fmt(v: Optional[float]) -> str:
                return f'{v:10.3f}' if v is not None else f'{"n/a":>10}'

            def fmt_d(v: Optional[float]) -> str:
                return f'{v:+10.3f}' if v is not None else f'{"n/a":>10}'

            print(
                f'{key:<8}  {fmt(a_was)}  {fmt(a_now)}  {fmt_d(d_a)}  '
                f'{fmt(t_was)}  {fmt(t_now)}  {fmt_d(d_t)}',
                flush=True,
            )

        print(
            '\nRising pressure with pump off usually indicates a leak or permeation. '
            'Small rise (<0.1 PSIA/hr) may be normal outgassing.',
            flush=True,
        )
        if args.restore:
            print('\nRestoring atmosphere...', flush=True)
            for key in ('port_a', 'port_b'):
                port = manager.get_port(key)
                if port is not None:
                    port.vent_to_atmosphere()
                    port.set_pressure(SAFE_SETPOINT_PSIA)
    finally:
        manager.disconnect_all(restore_safe_state=bool(args.restore))

    return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    """Read-only pressure log; leaves solenoid routing unchanged on exit."""
    config = load_config()
    manager = PortManager(config)
    manager.initialize_ports()
    if not manager.connect_all():
        print('ERROR: Could not connect hardware.', flush=True)
        return 1

    port_keys = tuple(args.ports.split(','))
    log_path: Optional[Path] = Path(args.log) if args.log else None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if not log_path.exists():
            log_path.write_text(
                'timestamp_utc,port,alicat_psia,transducer_psia\n',
                encoding='utf-8',
            )

    ports = [manager.get_port(k.strip()) for k in port_keys]
    ports = [p for p in ports if p is not None]

    print(
        'WARNING: monitor does NOT pull vacuum — only reads current pressures.\n'
        'For pull + 1 hr log use: vacuum_leak_test.py hold --duration 3600\n',
        flush=True,
    )
    print('Read-only (reconnect may restore vacuum route from saved DIO state). Ctrl+C to stop.\n', flush=True)
    _print_monitor_header()

    t0 = time.perf_counter()
    try:
        while True:
            _print_monitor_row(ports, log_path)
            if args.duration > 0 and (time.perf_counter() - t0) >= args.duration:
                break
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        print('\nStopped.', flush=True)
    finally:
        manager.disconnect_all(restore_safe_state=False)

    if log_path:
        print(f'Log appended: {log_path}', flush=True)
    return 0


def cmd_restore(_args: argparse.Namespace) -> int:
    config = load_config()
    manager = PortManager(config)
    manager.initialize_ports()
    if not manager.connect_all():
        print('ERROR: Could not connect hardware.', flush=True)
        return 1
    try:
        for key in ('port_a', 'port_b'):
            port = manager.get_port(key)
            if port is not None:
                port.vent_to_atmosphere()
                port.set_pressure(SAFE_SETPOINT_PSIA)
                print(f'{key}: atmosphere route', flush=True)
    finally:
        manager.disconnect_all(restore_safe_state=True)
    print('System restored to atmosphere.', flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Vacuum leak-down test (pull / check / restore).')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('pull', help='Pull vacuum, save baseline, leave on VACUUM route')

    p_hold = sub.add_parser(
        'hold',
        help='Pull vacuum until stable, then log (stays connected; recommended)',
    )
    p_hold.add_argument('--interval', type=float, default=60.0, help='Seconds between samples.')
    p_hold.add_argument(
        '--duration',
        type=float,
        default=3600.0,
        help='Seconds to log after stable vacuum (0 = until Ctrl+C).',
    )
    p_hold.add_argument('--log', default='logs/vacuum_leak_hold.csv', help='CSV log path.')
    p_hold.add_argument(
        '--restore',
        action='store_true',
        help='Vent to atmosphere when finished (default: stay on vacuum).',
    )

    p_check = sub.add_parser('check', help='Read pressures vs saved baseline')
    p_check.add_argument(
        '--restore',
        action='store_true',
        help='After check, vent both ports back to atmosphere.',
    )

    sub.add_parser('restore', help='Vent both ports to atmosphere')

    p_mon = sub.add_parser(
        'monitor',
        help='Timestamped pressure readings (read-only; safe during leak-down)',
    )
    p_mon.add_argument(
        '--interval',
        type=float,
        default=60.0,
        help='Seconds between samples (default: 60).',
    )
    p_mon.add_argument(
        '--duration',
        type=float,
        default=0.0,
        help='Stop after N seconds (0 = until Ctrl+C).',
    )
    p_mon.add_argument(
        '--ports',
        default='port_a,port_b',
        help='Comma-separated ports.',
    )
    p_mon.add_argument(
        '--log',
        default='',
        help='Optional CSV path to append readings (e.g. logs/vacuum_leak_monitor.csv).',
    )

    args = parser.parse_args()
    if args.command == 'pull':
        return cmd_pull(args)
    if args.command == 'hold':
        return cmd_hold(args)
    if args.command == 'check':
        return cmd_check(args)
    if args.command == 'restore':
        return cmd_restore(args)
    if args.command == 'monitor':
        return cmd_monitor(args)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
