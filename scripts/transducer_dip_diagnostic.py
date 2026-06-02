"""
Transducer voltage dip diagnostic.

Replays several read patterns (app poll loop, alternating AIN, DIO interleave,
sibling vacuum) and reports dip events with context.

Usage:
    python scripts/transducer_dip_diagnostic.py
    python scripts/transducer_dip_diagnostic.py --scenario app_poll --duration 30
    python scripts/transducer_dip_diagnostic.py --scenario all --duration 20
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_config
from app.hardware.alicat import AlicatController, AlicatController as AC
from app.hardware.labjack import LabJackController

DIP_VOLT = 0.25  # flag when sample drops this far below rolling median
ROLLING = 7


@dataclass
class DipEvent:
    scenario: str
    elapsed_s: float
    channel: str
    voltage: float
    median_v: float
    drop_v: float
    context: str


@dataclass
class ScenarioResult:
    name: str
    samples: int = 0
    dips: List[DipEvent] = field(default_factory=list)
    voltages_a: List[float] = field(default_factory=list)
    voltages_b: List[float] = field(default_factory=list)


def _build(config: Dict[str, Any]) -> Tuple[LabJackController, LabJackController, Dict[str, AlicatController]]:
    lj_cfg = config['hardware']['labjack']
    ali_cfg = config['hardware']['alicat']
    ca = LabJackController({**lj_cfg, **lj_cfg['port_a']})
    cb = LabJackController({**lj_cfg, **lj_cfg['port_b']})
    if not ca.configure() or not cb.configure():
        raise RuntimeError('LabJack configure failed')
    ca.set_solenoid(False)
    cb.set_solenoid(False)

    AC._shared_serials.clear()
    shared = None
    ctrls: Dict[str, AlicatController] = {}
    for key in ['port_a', 'port_b']:
        pc = ali_cfg[key]
        c = AlicatController({**ali_cfg, **pc, 'auto_tare_on_connect': False, 'auto_configure': False})
        if shared:
            c.set_shared_serial(shared)
        elif not c.connect():
            raise RuntimeError(f'Alicat connect failed for {key}')
        elif c._serial and c._owns_serial:
            shared = c._serial
        c.cancel_hold()
        ctrls[key] = c
    return ca, cb, ctrls


def _read_v(lj: LabJackController) -> Optional[float]:
    r = lj.read_transducer()
    return r.voltage if r else None


def _track(
    scenario: str,
    elapsed: float,
    va: Optional[float],
    vb: Optional[float],
    result: ScenarioResult,
    context: str,
    rolling_a: List[float],
    rolling_b: List[float],
) -> None:
    if va is not None:
        result.voltages_a.append(va)
        rolling_a.append(va)
        if len(rolling_a) > ROLLING:
            rolling_a.pop(0)
        if len(rolling_a) >= 3:
            med = statistics.median(rolling_a[:-1])
            if va < med - DIP_VOLT:
                result.dips.append(
                    DipEvent(scenario, elapsed, 'AIN0', va, med, med - va, context)
                )
    if vb is not None:
        result.voltages_b.append(vb)
        rolling_b.append(vb)
        if len(rolling_b) > ROLLING:
            rolling_b.pop(0)
        if len(rolling_b) >= 3:
            med = statistics.median(rolling_b[:-1])
            if vb < med - DIP_VOLT:
                result.dips.append(
                    DipEvent(scenario, elapsed, 'AIN2', vb, med, med - vb, context)
                )


def run_ain0_only(ca: LabJackController, duration: float) -> ScenarioResult:
    result = ScenarioResult('ain0_only')
    rolling_a: List[float] = []
    rolling_b: List[float] = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < duration:
        elapsed = time.perf_counter() - t0
        va = _read_v(ca)
        result.samples += 1
        _track('ain0_only', elapsed, va, None, result, 'ain0_only', rolling_a, rolling_b)
        time.sleep(0.007)
    return result


def run_alternate(ca: LabJackController, cb: LabJackController, duration: float) -> ScenarioResult:
    result = ScenarioResult('alternate_ain0_ain2')
    rolling_a: List[float] = []
    rolling_b: List[float] = []
    t0 = time.perf_counter()
    i = 0
    while time.perf_counter() - t0 < duration:
        elapsed = time.perf_counter() - t0
        va = _read_v(ca)
        vb = _read_v(cb)
        result.samples += 1
        ctx = 'after_b' if i % 2 else 'after_a'
        _track('alternate', elapsed, va, vb, result, ctx, rolling_a, rolling_b)
        i += 1
        time.sleep(0.007)
    return result


def run_correlation_style(
    ca: LabJackController,
    cb: LabJackController,
    ctrl: AlicatController,
    duration: float,
) -> ScenarioResult:
    result = ScenarioResult('correlation_style')
    rolling_a: List[float] = []
    rolling_b: List[float] = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < duration:
        elapsed = time.perf_counter() - t0
        va = _read_v(ca)
        vb = _read_v(cb)
        ctrl.read_status()
        result.samples += 1
        _track('correlation', elapsed, va, vb, result, 'ain+alicat', rolling_a, rolling_b)
        time.sleep(0.12)
    return result


def run_app_poll(
    ca: LabJackController,
    cb: LabJackController,
    ctrl_a: AlicatController,
    ctrl_b: AlicatController,
    duration: float,
    poll_ms: float,
    alicat_divisor: int,
) -> ScenarioResult:
    """Mimic PortManager._poll_loop read_fast path."""
    result = ScenarioResult('app_poll')
    rolling_a: List[float] = []
    rolling_b: List[float] = []
    countdown = { 'port_a': 0, 'port_b': 0 }
    t0 = time.perf_counter()
    cycle = 0
    while time.perf_counter() - t0 < duration:
        cycle_start = time.perf_counter()
        elapsed = cycle_start - t0
        cycle += 1

        for key, ctrl in [('port_a', ctrl_a), ('port_b', ctrl_b)]:
            if countdown[key] <= 0:
                ctrl.read_status()
                countdown[key] = max(0, alicat_divisor - 1)
            else:
                countdown[key] -= 1

        # port_a read_fast
        va = _read_v(ca)
        ca.read_switch_state()
        ca.read_dio_values(max_dio=22)
        vb_after_a = _read_v(cb)  # peek if reading dio on A affects B

        # port_b read_fast
        vb = _read_v(cb)
        cb.read_switch_state()
        cb.read_dio_values(max_dio=22)

        result.samples += 1
        ctx = f'cycle={cycle} alicat_refresh={countdown["port_a"]==alicat_divisor-1}'
        _track('app_poll', elapsed, va, vb, result, ctx, rolling_a, rolling_b)
        if vb_after_a is not None and vb is not None and abs(vb_after_a - vb) > DIP_VOLT:
            result.dips.append(
                DipEvent('app_poll', elapsed, 'AIN2_mid', vb_after_a, vb, vb - vb_after_a,
                         'ain2_changed_after_port_a_dio')
            )

        interval_s = poll_ms / 1000.0
        sleep_s = max(0.0, interval_s - (time.perf_counter() - cycle_start))
        time.sleep(sleep_s)
    return result


def run_vacuum_sibling(
    ca: LabJackController,
    cb: LabJackController,
    duration: float,
) -> ScenarioResult:
    """Port B on vacuum; monitor port A transducer for sibling dips."""
    result = ScenarioResult('vacuum_port_b_monitor_a')
    rolling_a: List[float] = []
    rolling_b: List[float] = []
    ca.set_solenoid(False)
    cb.set_solenoid(True)
    time.sleep(1.0)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < duration:
        elapsed = time.perf_counter() - t0
        va = _read_v(ca)
        vb = _read_v(cb)
        result.samples += 1
        _track('vacuum_b', elapsed, va, vb, result, 'b_vacuum', rolling_a, rolling_b)
        time.sleep(0.007)
    cb.set_solenoid(False)
    return result


def run_batch_read(ca: LabJackController, cb: LabJackController, duration: float) -> ScenarioResult:
    """Single eReadNames for both AIN channels (atomic batch)."""
    result = ScenarioResult('batch_eReadNames')
    rolling_a: List[float] = []
    rolling_b: List[float] = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < duration:
        elapsed = time.perf_counter() - t0
        vals = ca._read_names_with_retry(['AIN0', 'AIN2'])
        va = vals[0] if vals else None
        vb = vals[1] if vals and len(vals) > 1 else None
        result.samples += 1
        _track('batch', elapsed, va, vb, result, 'batch', rolling_a, rolling_b)
        time.sleep(0.007)
    return result


def _summarize(result: ScenarioResult) -> None:
    def stats(vs: List[float]) -> str:
        if len(vs) < 2:
            return 'n/a'
        return (
            f'mean={statistics.mean(vs):.4f} std={statistics.stdev(vs)*1000:.1f}mV '
            f'pp={(max(vs)-min(vs))*1000:.1f}mV'
        )

    dip_a = sum(1 for d in result.dips if d.channel.startswith('AIN0'))
    dip_b = sum(1 for d in result.dips if d.channel.startswith('AIN2'))
    print(f'\n--- {result.name} ({result.samples} samples) ---')
    print(f'  AIN0: {stats(result.voltages_a)}  dips={dip_a}')
    print(f'  AIN2: {stats(result.voltages_b)}  dips={dip_b}')
    if result.dips:
        print('  First dips:')
        for d in result.dips[:8]:
            print(
                f'    t={d.elapsed_s:6.2f}s {d.channel} {d.voltage:.4f}V '
                f'(med {d.median_v:.4f}, drop {d.drop_v*1000:.0f}mV) [{d.context}]'
            )


def main() -> int:
    parser = argparse.ArgumentParser(description='Transducer dip diagnostic')
    parser.add_argument('--duration', type=float, default=25.0, help='Seconds per scenario')
    parser.add_argument(
        '--scenario',
        choices=['all', 'ain0_only', 'alternate', 'correlation', 'app_poll', 'vacuum', 'batch'],
        default='all',
    )
    parser.add_argument('--poll-ms', type=float, default=7.0)
    parser.add_argument('--alicat-divisor', type=int, default=14)
    args = parser.parse_args()

    config = load_config()
    ca, cb, ctrls = _build(config)
    results: List[ScenarioResult] = []

    try:
        scenarios: List[Tuple[str, Callable[[], ScenarioResult]]] = []
        if args.scenario in ('all', 'ain0_only'):
            scenarios.append(('ain0_only', lambda: run_ain0_only(ca, args.duration)))
        if args.scenario in ('all', 'alternate'):
            scenarios.append(('alternate', lambda: run_alternate(ca, cb, args.duration)))
        if args.scenario in ('all', 'correlation'):
            scenarios.append(('correlation', lambda: run_correlation_style(ca, cb, ctrls['port_a'], args.duration)))
        if args.scenario in ('all', 'app_poll'):
            scenarios.append(('app_poll', lambda: run_app_poll(
                ca, cb, ctrls['port_a'], ctrls['port_b'], args.duration, args.poll_ms, args.alicat_divisor,
            )))
        if args.scenario in ('all', 'batch'):
            scenarios.append(('batch', lambda: run_batch_read(ca, cb, args.duration)))
        if args.scenario in ('all', 'vacuum'):
            scenarios.append(('vacuum', lambda: run_vacuum_sibling(ca, cb, args.duration)))

        print('=' * 72)
        print('Transducer dip diagnostic')
        print(f'  resolution_index={config["hardware"]["labjack"].get("resolution_index")}')
        print(f'  dip threshold={DIP_VOLT}V below rolling median')
        print('=' * 72)

        for name, fn in scenarios:
            print(f'\nRunning {name} for {args.duration:.0f}s ...')
            results.append(fn())

        print('\n' + '=' * 72)
        print('SUMMARY')
        print('=' * 72)
        for r in results:
            _summarize(r)

        # Compare dip rates
        print('\n--- Dip rate comparison ---')
        for r in results:
            total_dips = len([d for d in r.dips if 'mid' not in d.channel])
            rate = 100.0 * total_dips / max(1, r.samples)
            print(f'  {r.name:28} {total_dips:4} dips / {r.samples:4} samples ({rate:.1f}%)')

    finally:
        ca.set_solenoid(False)
        cb.set_solenoid(False)
        ca.cleanup()
        cb.cleanup()

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
