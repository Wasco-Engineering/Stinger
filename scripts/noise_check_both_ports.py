#!/usr/bin/env python3
"""Measure static transducer noise on port_a and port_b (exhaust, EMA off).

Default timing targets ~10s wall time for both ports (short settle + timed capture).
Override with ``--total-seconds``.

Slowness this avoids: (1) Windows ``comports()`` enumeration on every Alicat open —
cached in ``alicat.py`` for 15s; (2) Alicat post-connect DCU/LSS chatter — disabled
here via ``auto_configure=False``; (3) serial status polls in the ADC loop — only the
LabJack transducer is sampled in the hot path (one Alicat snapshot after capture).
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_config
from app.hardware.alicat import AlicatController
from app.hardware.labjack import LabJackController


def build_labjack(lj_cfg: dict, port: str) -> LabJackController:
    port_cfg = lj_cfg.get(port, {})
    return LabJackController(
        {
            'device_type': lj_cfg.get('device_type', 'T7'),
            'connection_type': lj_cfg.get('connection_type', 'USB'),
            'identifier': lj_cfg.get('identifier', 'ANY'),
            'resolution_index': lj_cfg.get('resolution_index', 9),
            'pressure_filter_alpha': 0.0,
            **port_cfg,
        }
    )


def build_alicat(al_cfg: dict, port: str) -> AlicatController:
    port_cfg = al_cfg.get(port, {})
    return AlicatController(
        {
            'com_port': port_cfg.get('com_port'),
            'address': port_cfg.get('address'),
            'baudrate': al_cfg.get('baudrate', 115200),
            'timeout_s': al_cfg.get('timeout_s', 0.05),
            'pressure_index': al_cfg.get('pressure_index'),
            'setpoint_index': al_cfg.get('setpoint_index'),
            'gauge_index': al_cfg.get('gauge_index'),
            'barometric_index': al_cfg.get('barometric_index'),
            'pressure_units_stat': al_cfg.get('pressure_units_stat'),
            'pressure_units_group': al_cfg.get('pressure_units_group'),
            'pressure_units_value': al_cfg.get('pressure_units_value'),
            'pressure_units_override': al_cfg.get('pressure_units_override'),
            'auto_tare_on_connect': False,
            'auto_configure': False,
        }
    )


def measure(
    port: str,
    lj_cfg: dict,
    al_cfg: dict,
    *,
    settle_s: float,
    sample_duration_s: float,
) -> None:
    print(f'\n=== {port} noise (exhaust, EMA off, pressure_raw) ===', flush=True)
    labjack = build_labjack(lj_cfg, port)
    alicat = build_alicat(al_cfg, port)
    print('  LabJack configure…', flush=True)
    if not labjack.configure():
        print(f'LabJack configure failed: {labjack._last_status}', flush=True)
        return
    print('  Alicat connect…', flush=True)
    if not alicat.connect():
        print(f'Alicat connect failed: {alicat._last_status}', flush=True)
        labjack.cleanup()
        return
    try:
        alicat.exhaust()
        time.sleep(settle_s)
        voltages: list[float] = []
        pressures: list[float] = []
        start = time.perf_counter()
        end = start + sample_duration_s
        print('  Sampling LabJack only (hot loop)…', flush=True)
        while time.perf_counter() < end:
            trans = labjack.read_transducer()
            if trans is not None:
                voltages.append(trans.voltage)
                pressures.append(trans.pressure_raw)
        elapsed = time.perf_counter() - start
        if len(voltages) < 2:
            print('Not enough samples', flush=True)
            return
        v_std = statistics.stdev(voltages)
        p_std = statistics.stdev(pressures)
        alicat_snap = alicat.read_status()
        al_psi = alicat_snap.pressure if alicat_snap else float('nan')
        print(f'samples: {len(voltages)} in {elapsed:.2f}s ({len(voltages) / elapsed:.1f} Hz)', flush=True)
        print(
            f'trans V: mean={statistics.mean(voltages):.5f} std={v_std * 1000:.3f} mV '
            f'p-p={(max(voltages) - min(voltages)) * 1000:.3f} mV',
            flush=True,
        )
        print(
            f'trans PSIA: mean={statistics.mean(pressures):.4f} std={p_std:.4f} '
            f'p-p={(max(pressures) - min(pressures)):.4f}',
            flush=True,
        )
        print(f'alicat PSIA (single read after capture): {al_psi:.4f}', flush=True)
    finally:
        try:
            alicat.exhaust()
        except Exception:
            pass
        alicat.disconnect()
        labjack.cleanup()


def _per_port_timing(total_seconds: float) -> tuple[float, float]:
    """Split ``total_seconds`` across two ports: settle + acquisition each."""
    budget = max(total_seconds / 2.0, 2.0)
    settle_s = min(1.0, max(0.25, budget * 0.12))
    sample_duration_s = max(1.5, budget - settle_s)
    return settle_s, sample_duration_s


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--total-seconds',
        type=float,
        default=10.0,
        metavar='T',
        help='Approximate wall time budget split across port_a and port_b (default: 10)',
    )
    args = parser.parse_args()
    settle_s, sample_duration_s = _per_port_timing(args.total_seconds)

    config = load_config()
    lj_cfg = config.get('hardware', {}).get('labjack', {})
    al_cfg = config.get('hardware', {}).get('alicat', {})
    print(
        f'Per port: settle {settle_s:.2f}s, capture {sample_duration_s:.2f}s '
        f'(from --total-seconds {args.total_seconds:g})',
        flush=True,
    )
    for p in ('port_a', 'port_b'):
        measure(p, lj_cfg, al_cfg, settle_s=settle_s, sample_duration_s=sample_duration_s)


if __name__ == '__main__':
    main()
