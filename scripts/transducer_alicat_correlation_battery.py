"""
Transducer vs Alicat correlation battery (static + dynamic).

Usage (from repo root):
    python scripts/transducer_alicat_correlation_battery.py
    python scripts/transducer_alicat_correlation_battery.py --port port_a
    python scripts/transducer_alicat_correlation_battery.py --static 0.2 2 5 14 30 --dynamic-end 30
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_config
from app.hardware.alicat import AlicatController, AlicatController as AC
from app.hardware.labjack import LabJackController

SAFE_SP = 0.2
# Full 0–30 PSIA sweep: low band uses vacuum routing, high band uses atmosphere.
STATIC_LOW_VACUUM = [0.2, 2.0, 5.0, 8.0]
STATIC_HIGH_ATM = [10.0, 14.0, 18.0, 22.0, 26.0, 28.0, 30.0]
STATIC_SETTLE_S = 10.0
STATIC_SETTLE_LOW_S = 14.0
STATIC_SAMPLES = 25
DYNAMIC_START = 0.2
DYNAMIC_END = 30.0
DYNAMIC_RATE_PSI_S = 1.0
DYNAMIC_SAMPLE_INTERVAL_S = 0.25
VACUUM_BELOW_PSI = 12.0  # route vacuum when setpoint below this (near/atmosphere crossover)


@dataclass
class Sample:
    elapsed_s: float
    phase: str
    port_key: str
    alicat_addr: str
    target_sp: float
    alicat_p: Optional[float]
    transducer_p: Optional[float]
    transducer_v: Optional[float]
    offset: Optional[float]  # transducer - alicat


def _setup(config: Dict[str, Any]) -> Tuple[LabJackController, LabJackController, Dict[str, AlicatController], Any]:
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
            raise RuntimeError(f'Alicat connect failed for {key}: {c._last_status}')
        elif c._serial and c._owns_serial:
            shared = c._serial
        c.cancel_hold()
        ctrls[key] = c
    return ca, cb, ctrls, shared


def _read_pair(
    ca: LabJackController,
    cb: LabJackController,
    active_key: str,
    ctrl: AlicatController,
    addr: str,
    phase: str,
    elapsed: float,
    target_sp: float,
) -> Tuple[Sample, Sample]:
    ra = ca.read_transducer()
    rb = cb.read_transducer()
    r = ctrl.read_status()
    al_p = r.pressure if r else None

    def make(port_key: str, tr) -> Sample:
        tp = tr.pressure if tr else None
        off = (tp - al_p) if (tp is not None and al_p is not None) else None
        return Sample(
            elapsed_s=elapsed,
            phase=phase,
            port_key=port_key,
            alicat_addr=addr,
            target_sp=target_sp,
            alicat_p=al_p,
            transducer_p=tp,
            transducer_v=tr.voltage if tr else None,
            offset=off,
        )

    return make('port_a', ra), make('port_b', rb)


def _hold_other(ctrls: Dict[str, AlicatController], active_key: str) -> None:
    other = 'port_b' if active_key == 'port_a' else 'port_a'
    ctrls[other].set_pressure(SAFE_SP)


def _route_for_target(lj: LabJackController, target: float) -> None:
    """Vacuum solenoid for low absolute setpoints, atmosphere otherwise."""
    lj.set_solenoid(to_vacuum=(target < VACUUM_BELOW_PSI))


def _static_step(
    port_key: str,
    target: float,
    ca: LabJackController,
    cb: LabJackController,
    ctrls: Dict[str, AlicatController],
    ali_cfg: Dict[str, Any],
    use_vacuum: bool,
) -> Tuple[List[Sample], float, float, float, float, float]:
    ctrl = ctrls[port_key]
    addr = ali_cfg[port_key]['address']
    lj = ca if port_key == 'port_a' else cb
    _hold_other(ctrls, port_key)
    lj.set_solenoid(to_vacuum=use_vacuum)
    ctrl.cancel_hold()
    ctrl.set_pressure(target)
    settle_s = STATIC_SETTLE_LOW_S if use_vacuum else STATIC_SETTLE_S
    time.sleep(settle_s)

    batch: List[Sample] = []
    t0 = time.perf_counter()
    for _ in range(STATIC_SAMPLES):
        sa, sb = _read_pair(
            ca, cb, port_key, ctrl, addr, 'static', time.perf_counter() - t0, target,
        )
        batch.append(sa if port_key == 'port_a' else sb)
        time.sleep(0.12)

    offsets = [b.offset for b in batch if b.offset is not None]
    al_means = [b.alicat_p for b in batch if b.alicat_p is not None]
    tr_means = [b.transducer_p for b in batch if b.transducer_p is not None]
    volts = [b.transducer_v for b in batch if b.transducer_v is not None]
    mean_off = statistics.mean(offsets) if offsets else float('nan')
    std_off = statistics.stdev(offsets) if len(offsets) > 1 else 0.0
    return (
        batch,
        statistics.mean(al_means) if al_means else float('nan'),
        statistics.mean(tr_means) if tr_means else float('nan'),
        statistics.mean(volts) if volts else float('nan'),
        mean_off,
        std_off,
    )


def run_static(
    port_key: str,
    ca: LabJackController,
    cb: LabJackController,
    ctrls: Dict[str, AlicatController],
    ali_cfg: Dict[str, Any],
    static_targets: Optional[List[float]] = None,
) -> List[Sample]:
    ctrl = ctrls[port_key]
    addr = ali_cfg[port_key]['address']
    lj = ca if port_key == 'port_a' else cb
    out: List[Sample] = []
    print(f'\n=== STATIC {port_key} (Alicat {addr}) 0–30 PSIA ===')
    print(f'{"Target":>6} {"Route":>5} {"Alicat":>8} {"T-paired":>8} {"V":>7} {"Offset":>8} {"StdOff":>7}')

    if static_targets is None:
        steps: List[Tuple[float, bool]] = (
            [(t, True) for t in STATIC_LOW_VACUUM]
            + [(t, False) for t in STATIC_HIGH_ATM]
        )
    else:
        steps = [(t, t < VACUUM_BELOW_PSI) for t in static_targets]

    prev_vacuum: Optional[bool] = None
    for target, use_vacuum in steps:
        if prev_vacuum is True and not use_vacuum:
            # Vacuum band -> atmosphere pressurization: vent before mid-range targets.
            lj.set_solenoid(False)
            ctrl.cancel_hold()
            ctrl.set_pressure(SAFE_SP)
            print(f'  ... venting to atmosphere before {target:.1f} PSIA')
            time.sleep(12.0)
        prev_vacuum = use_vacuum
        batch, al_m, tr_m, v_m, mean_off, std_off = _static_step(
            port_key, target, ca, cb, ctrls, ali_cfg, use_vacuum,
        )
        out.extend(batch)
        route = 'vac' if use_vacuum else 'atm'
        print(
            f'{target:6.1f} {route:>5} '
            f'{al_m:8.2f} {tr_m:8.2f} {v_m:7.3f} '
            f'{mean_off:+8.3f} {std_off:7.3f}'
        )

    lj.set_solenoid(False)
    ctrl.set_pressure(SAFE_SP)
    return out


def run_dynamic(
    port_key: str,
    ca: LabJackController,
    cb: LabJackController,
    ctrls: Dict[str, AlicatController],
    ali_cfg: Dict[str, Any],
    dynamic_start: float = DYNAMIC_START,
    dynamic_end: float = DYNAMIC_END,
    rate_psi_s: float = DYNAMIC_RATE_PSI_S,
) -> List[Sample]:
    ctrl = ctrls[port_key]
    addr = ali_cfg[port_key]['address']
    lj = ca if port_key == 'port_a' else cb
    _hold_other(ctrls, port_key)

    # Prep: pull to dynamic_start on vacuum (may take a while if coming from high pressure).
    _route_for_target(lj, dynamic_start)
    ctrl.set_ramp_rate(max(rate_psi_s, 2.0))
    ctrl.cancel_hold()
    ctrl.set_pressure(dynamic_start)
    print(f'  ... pulling to {dynamic_start:.1f} PSIA on vacuum before ramp')
    prep_t0 = time.perf_counter()
    while time.perf_counter() - prep_t0 < 90.0:
        r = ctrl.read_status()
        p = r.pressure if r else None
        if p is not None and p <= dynamic_start + 0.5:
            break
        time.sleep(1.0)
    ctrl.set_ramp_rate(rate_psi_s)
    time.sleep(2.0)
    ctrl.cancel_hold()
    ctrl.set_pressure(dynamic_end)

    out: List[Sample] = []
    t0 = time.perf_counter()
    print(
        f'\n=== DYNAMIC {port_key} (Alicat {addr}) '
        f'{dynamic_start}->{dynamic_end} @ {rate_psi_s} PSI/s ==='
    )
    print(f'{"t":>5} {"Alicat":>8} {"T-paired":>8} {"Offset":>8} {"Route":>5}')

    duration = abs(dynamic_end - dynamic_start) / rate_psi_s
    last_route: Optional[bool] = True  # start on vacuum after prep
    while True:
        elapsed = time.perf_counter() - t0
        if elapsed > duration + 8.0:
            break
        r = ctrl.read_status()
        current_p = r.pressure if r else None
        to_vacuum = (current_p or dynamic_end) < VACUUM_BELOW_PSI
        if last_route is None or to_vacuum != last_route:
            lj.set_solenoid(to_vacuum=to_vacuum)
            last_route = to_vacuum
        sa, sb = _read_pair(
            ca, cb, port_key, ctrl, addr, 'dynamic', elapsed, dynamic_end,
        )
        s = sa if port_key == 'port_a' else sb
        out.append(s)
        if s.alicat_p is not None and s.transducer_p is not None:
            route = 'vac' if to_vacuum else 'atm'
            print(f'{elapsed:5.1f} {s.alicat_p:8.2f} {s.transducer_p:8.2f} {s.offset:+8.3f} {route:>5}')
        time.sleep(DYNAMIC_SAMPLE_INTERVAL_S)

    lj.set_solenoid(False)
    ctrl.set_pressure(SAFE_SP)
    time.sleep(3.0)
    return out


def summarize(samples: List[Sample], port_key: str) -> None:
    paired = [s for s in samples if s.port_key == port_key and s.offset is not None]
    if not paired:
        return
    by_phase: Dict[str, List[float]] = {}
    for s in paired:
        by_phase.setdefault(s.phase, []).append(s.offset)
    print(f'\n--- Summary {port_key} ---')
    for phase, offs in by_phase.items():
        print(
            f'  {phase}: n={len(offs)} mean_offset={statistics.mean(offs):+.3f} '
            f'std={statistics.stdev(offs) if len(offs) > 1 else 0:.3f} '
            f'min={min(offs):+.3f} max={max(offs):+.3f}'
        )

    static = [s for s in paired if s.phase == 'static']
    if static:
        # Per-target static offset table
        by_target: Dict[float, List[float]] = {}
        for s in static:
            by_target.setdefault(s.target_sp, []).append(s.offset)
        print('  static offset by target (transducer - alicat):')
        for target in sorted(by_target):
            offs = by_target[target]
            print(
                f'    SP={target:5.1f}  mean={statistics.mean(offs):+.3f} '
                f'std={statistics.stdev(offs) if len(offs) > 1 else 0:.3f} PSI'
            )

    static_v = [s for s in paired if s.phase == 'static' and s.transducer_v is not None]
    if static_v:
        max_v = max(s.transducer_v for s in static_v if s.transducer_v is not None)
        max_tr_p = max(s.transducer_p for s in static_v if s.transducer_p is not None)
        max_al_p = max(s.alicat_p for s in static_v if s.alicat_p is not None)
        print(f'  transducer max voltage seen: {max_v:.3f} V')
        print(f'  transducer max pressure seen: {max_tr_p:.2f} PSI')
        print(f'  alicat max during static: {max_al_p:.2f} PSI')


def write_csv(path: Path, samples: List[Sample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow([
            'elapsed_s', 'phase', 'port', 'alicat_addr', 'target_sp',
            'alicat_psi', 'transducer_psi', 'transducer_v', 'offset',
        ])
        for s in samples:
            w.writerow([
                f'{s.elapsed_s:.3f}', s.phase, s.port_key, s.alicat_addr,
                f'{s.target_sp:.2f}',
                '' if s.alicat_p is None else f'{s.alicat_p:.4f}',
                '' if s.transducer_p is None else f'{s.transducer_p:.4f}',
                '' if s.transducer_v is None else f'{s.transducer_v:.4f}',
                '' if s.offset is None else f'{s.offset:.4f}',
            ])


def cleanup(
    ca: LabJackController,
    cb: LabJackController,
    ctrls: Dict[str, AlicatController],
    shared: Any,
) -> None:
    for c in ctrls.values():
        try:
            c.set_pressure(SAFE_SP)
        except Exception:
            pass
    ca.cleanup()
    cb.cleanup()
    if shared:
        AC._shared_serials.clear()
        try:
            shared.close()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description='Transducer/Alicat correlation battery')
    parser.add_argument('--port', choices=['port_a', 'port_b', 'both'], default='both')
    parser.add_argument('--static', type=float, nargs='*', help='Override static target list (PSIA)')
    parser.add_argument('--dynamic-start', type=float, default=DYNAMIC_START)
    parser.add_argument('--dynamic-end', type=float, default=DYNAMIC_END)
    parser.add_argument('--dynamic-rate', type=float, default=DYNAMIC_RATE_PSI_S)
    args = parser.parse_args()
    ports = ['port_a', 'port_b'] if args.port == 'both' else [args.port]
    static_targets = args.static if args.static else None

    config = load_config()
    ali_cfg = config['hardware']['alicat']
    ca, cb, ctrls, shared = _setup(config)
    all_samples: List[Sample] = []

    try:
        for port_key in ports:
            all_samples.extend(
                run_static(port_key, ca, cb, ctrls, ali_cfg, static_targets=static_targets)
            )
            time.sleep(2.0)
            all_samples.extend(
                run_dynamic(
                    port_key, ca, cb, ctrls, ali_cfg,
                    dynamic_start=args.dynamic_start,
                    dynamic_end=args.dynamic_end,
                    rate_psi_s=args.dynamic_rate,
                )
            )
            summarize(all_samples, port_key)
            time.sleep(2.0)
    finally:
        cleanup(ca, cb, ctrls, shared)

    ts = time.strftime('%Y%m%d_%H%M%S')
    out_path = PROJECT_ROOT / 'scripts' / 'data' / f'transducer_alicat_battery_{ts}.csv'
    write_csv(out_path, all_samples)
    print(f'\nCSV: {out_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
