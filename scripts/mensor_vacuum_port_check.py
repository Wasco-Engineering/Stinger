#!/usr/bin/env python3
"""Vacuum pull on one port with Mensor offset snapshot (reference on port_b tee).

Pulls vacuum on the selected port, stabilizes, then logs Mensor vs Alicat vs transducer
offsets. Use on Stand 1 with Mensor on the right (port_b) tee.

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\mensor_vacuum_port_check.py --port port_b
    .\\.venv\\Scripts\\python.exe scripts\\mensor_vacuum_port_check.py --port port_b --discover-mensor
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
from app.core.paths import get_config_dir, get_logs_dir
from app.hardware.port import PortManager
from quality_cal.config import get_default_config_path, load_config as load_qc_config
from quality_cal.core.hardware_discovery import discover_mensor_port
from quality_cal.core.mensor_reader import MensorReader

SAFE_SETPOINT_PSIA = 0.2
PULL_SETPOINT_PSIA = 0.0
STABLE_MAX_PSIA = 0.15
STABLE_SECONDS = 8.0
SAMPLE_COUNT = 5
SAMPLE_INTERVAL_S = 1.0


def _baro(reading) -> float:
    if reading and reading.alicat and reading.alicat.barometric_pressure is not None:
        return float(reading.alicat.barometric_pressure)
    return 14.7


def _transducer_psi(reading, baro: float) -> Optional[float]:
    if not reading or not reading.transducer:
        return None
    p = float(reading.transducer.pressure)
    ref = str(reading.transducer.pressure_reference or 'absolute').lower()
    if ref == 'gauge':
        return p + baro
    return p


def _alicat_psi(reading) -> Optional[float]:
    if reading and reading.alicat and reading.alicat.pressure is not None:
        return float(reading.alicat.pressure)
    return None


def _read_mensor(reader: MensorReader) -> Optional[float]:
    try:
        sample = reader.read_pressure()
        if sample is None:
            return None
        return float(sample.pressure_psia)
    except Exception:
        return None


def _wait_stable(port, max_psia: float, hold_s: float) -> bool:
    stable_since: Optional[float] = None
    deadline = time.perf_counter() + 120.0
    while time.perf_counter() < deadline:
        r = port.read_all()
        ap = _alicat_psi(r)
        if ap is None:
            time.sleep(0.5)
            continue
        if float(ap) <= max_psia:
            if stable_since is None:
                stable_since = time.perf_counter()
            elif time.perf_counter() - stable_since >= hold_s:
                return True
        else:
            stable_since = None
        print(f'  alicat={ap:.3f} psia', flush=True)
        time.sleep(1.0)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description='Vacuum + Mensor offset check (one port).')
    parser.add_argument('--port', default='port_b', choices=('port_a', 'port_b'))
    parser.add_argument('--discover-mensor', action='store_true')
    parser.add_argument('--restore', action='store_true', help='Vent to atmosphere on exit.')
    args = parser.parse_args()

    config = load_config()
    qc_config = load_qc_config(get_default_config_path())
    mensor_cfg = dict(qc_config.get('hardware', {}).get('mensor', {}) or {})
    if args.discover_mensor:
        excluded = set()
        for key in ('port_a', 'port_b'):
            com = str(config.get('hardware', {}).get('alicat', {}).get(key, {}).get('com_port', ''))
            if com:
                excluded.add(com)
        found = discover_mensor_port(qc_config, exclude_ports=excluded)
        if not found:
            print('ERROR: Mensor discovery failed.', flush=True)
            return 1
        mensor_cfg['port'] = found
        print(f'Mensor discovered on {found}', flush=True)

    port_name = str(mensor_cfg.get('port', '')).strip()
    if not port_name:
        print('ERROR: No Mensor COM port in quality_cal_config.yaml', flush=True)
        return 1

    manager = PortManager(config)
    manager.initialize_ports()
    if not manager.connect_all():
        print('ERROR: Hardware connect failed.', flush=True)
        return 1

    port = manager.get_port(args.port)
    if port is None:
        print(f'ERROR: {args.port} not found.', flush=True)
        return 1

    mensor = MensorReader(mensor_cfg)
    if not mensor.connect():
        print('ERROR: Mensor connect failed.', flush=True)
        manager.disconnect_all(restore_safe_state=bool(args.restore))
        return 1

    restore = bool(args.restore)
    try:
        print(f'Config dir: {get_config_dir()}', flush=True)
        print(f'Port: {args.port} | Mensor: {port_name}', flush=True)
        print('Venting, then pulling vacuum...', flush=True)
        port.vent_to_atmosphere()
        port.set_pressure(SAFE_SETPOINT_PSIA)
        time.sleep(2.0)
        port.alicat.cancel_hold()
        port.set_pressure(PULL_SETPOINT_PSIA)
        time.sleep(0.5)
        if not port.set_solenoid(True):
            print('ERROR: Vacuum route refused (pressure too high?).', flush=True)
            return 1
        print('Waiting for stable vacuum...', flush=True)
        if not _wait_stable(port, STABLE_MAX_PSIA, STABLE_SECONDS):
            print('WARNING: Stable vacuum not confirmed.', flush=True)

        samples: list[Dict[str, Any]] = []
        for i in range(SAMPLE_COUNT):
            r = port.read_all()
            b = _baro(r)
            m = _read_mensor(mensor)
            a = _alicat_psi(r)
            t = _transducer_psi(r, b)
            row = {
                'mensor_psia': m,
                'alicat_psia': a,
                'transducer_psia': t,
                'alicat_minus_mensor': (a - m) if a is not None and m is not None else None,
                'transducer_minus_mensor': (t - m) if t is not None and m is not None else None,
            }
            samples.append(row)
            print(
                f'  [{i + 1}/{SAMPLE_COUNT}] mensor={m} alicat={a} trans={t} '
                f'dA={row["alicat_minus_mensor"]} dT={row["transducer_minus_mensor"]}',
                flush=True,
            )
            time.sleep(SAMPLE_INTERVAL_S)

        def _mean(key: str) -> Optional[float]:
            vals = [s[key] for s in samples if s.get(key) is not None]
            if not vals:
                return None
            return sum(float(v) for v in vals) / len(vals)

        summary = {
            'checked_at': datetime.now(timezone.utc).isoformat(),
            'port': args.port,
            'mensor_com': port_name,
            'mean_mensor_psia': _mean('mensor_psia'),
            'mean_alicat_psia': _mean('alicat_psia'),
            'mean_transducer_psia': _mean('transducer_psia'),
            'mean_alicat_minus_mensor': _mean('alicat_minus_mensor'),
            'mean_transducer_minus_mensor': _mean('transducer_minus_mensor'),
            'samples': samples,
        }
        out = get_logs_dir() / f'mensor_vacuum_{args.port}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2), encoding='utf-8')

        print('\n--- MEAN OFFSETS (sensor − Mensor) ---', flush=True)
        print(f'  Alicat − Mensor:      {summary["mean_alicat_minus_mensor"]}', flush=True)
        print(f'  Transducer − Mensor:  {summary["mean_transducer_minus_mensor"]}', flush=True)
        print(f'\nSaved: {out}', flush=True)
    finally:
        mensor.disconnect()
        if restore:
            port.vent_to_atmosphere()
            port.set_pressure(SAFE_SETPOINT_PSIA)
        manager.disconnect_all(restore_safe_state=restore)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
