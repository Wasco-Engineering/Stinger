"""
Vacuum pull test: solenoid to vacuum + Alicat low setpoints.

Verifies each port can pull down with the vacuum pump and detects possible
solenoid port swap (wrong transducer moves when sibling solenoid toggles).

Usage (from repo root):
    python scripts/vacuum_pull_test.py
    python scripts/vacuum_pull_test.py --targets 5 2 0.5 0.2
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_config
from app.hardware.alicat import AlicatController, AlicatController as AC
from app.hardware.labjack import LabJackController

SAFE_SP = 0.2
DEFAULT_TARGETS = [8.0, 5.0, 2.0, 0.5, 0.2]
SETTLE_S = 12.0


def _setup(config: Dict[str, Any]) -> Tuple[LabJackController, LabJackController, Dict[str, AlicatController], Any, Dict]:
    lj_cfg = config['hardware']['labjack']
    ali_cfg = config['hardware']['alicat']
    ca = LabJackController({**lj_cfg, **lj_cfg['port_a']})
    cb = LabJackController({**lj_cfg, **lj_cfg['port_b']})
    if not ca.configure() or not cb.configure():
        raise RuntimeError('LabJack configure failed')

    AC._shared_serials.clear()
    shared = None
    ctrls: Dict[str, AlicatController] = {}
    for key in ['port_a', 'port_b']:
        pc = ali_cfg[key]
        c = AlicatController({**ali_cfg, **pc, 'auto_tare_on_connect': False, 'auto_configure': False})
        if shared:
            c.set_shared_serial(shared)
        elif not c.connect():
            raise RuntimeError(f'Alicat {key} connect failed: {c._last_status}')
        elif c._serial and c._owns_serial:
            shared = c._serial
        c.cancel_hold()
        ctrls[key] = c

    ca.set_solenoid(False)
    cb.set_solenoid(False)
    return ca, cb, ctrls, shared, ali_cfg


def _read_all(
    ca: LabJackController,
    cb: LabJackController,
    ctrls: Dict[str, AlicatController],
    ali_cfg: Dict,
) -> Dict[str, Optional[float]]:
    ra, rb = ca.read_transducer(), cb.read_transducer()
    out: Dict[str, Optional[float]] = {
        'T0/1': ra.pressure if ra else None,
        'T2/3': rb.pressure if rb else None,
    }
    for key, c in ctrls.items():
        addr = ali_cfg[key]['address']
        r = c.read_status()
        out[f'alicat_{key}'] = r.pressure if r else None
        out[f'sp_{key}'] = r.setpoint if r else None
        out[f'addr_{key}'] = addr
    return out


def _print_row(label: str, data: Dict[str, Optional[float]]) -> None:
    def fmt(v: Optional[float]) -> str:
        return f'{v:7.2f}' if v is not None else '    n/a'

    print(
        f'  {label:18} '
        f'port_a(B) P={fmt(data.get("alicat_port_a"))} SP={fmt(data.get("sp_port_a"))} | '
        f'port_b(A) P={fmt(data.get("alicat_port_b"))} SP={fmt(data.get("sp_port_b"))} | '
        f'T0/1={fmt(data.get("T0/1"))} T2/3={fmt(data.get("T2/3"))}'
    )


def _safe_for_vacuum(alicat_p: Optional[float], baro: float = 14.7, margin: float = 2.0) -> bool:
    if alicat_p is None:
        return False
    return alicat_p <= baro + margin


def run_port_vacuum_pull(
    active_key: str,
    targets: List[float],
    ca: LabJackController,
    cb: LabJackController,
    ctrls: Dict[str, AlicatController],
    ali_cfg: Dict,
) -> None:
    other_key = 'port_b' if active_key == 'port_a' else 'port_a'
    active_lj = ca if active_key == 'port_a' else cb
    other_lj = cb if active_key == 'port_a' else ca
    active_ctrl = ctrls[active_key]
    other_ctrl = ctrls[other_key]
    addr = ali_cfg[active_key]['address']
    paired_ain = 'T0/1' if active_key == 'port_a' else 'T2/3'
    dio = active_lj.solenoid_dio

    print(f'\n{"="*72}')
    print(f'VACUUM PULL — {active_key} (Alicat {addr}, solenoid DIO{dio}, paired {paired_ain})')
    print(f'{"="*72}')

    # Hold sibling at safe setpoint, atmosphere route
    other_lj.set_solenoid(False)
    other_ctrl.set_pressure(SAFE_SP)
    active_lj.set_solenoid(False)
    active_ctrl.set_pressure(SAFE_SP)
    time.sleep(3.0)
    baseline = _read_all(ca, cb, ctrls, ali_cfg)
    _print_row('baseline (both atm)', baseline)

    al_key = f'alicat_{active_key}'
    if not _safe_for_vacuum(baseline.get(al_key)):
        print(f'  WARN: {active_key} Alicat pressure not near atmosphere — vent before vacuum')
        active_ctrl.set_pressure(SAFE_SP)
        time.sleep(4.0)

    print(f'  -> solenoid DIO{dio} VACUUM')
    if not active_lj.set_solenoid(True):
        print('  FAIL: could not set solenoid to vacuum')
        return

    time.sleep(1.0)
    _print_row('after vacuum route', _read_all(ca, cb, ctrls, ali_cfg))

    for target in targets:
        print(f'\n  --- setpoint {target:.2f} PSIA ---')
        active_ctrl.cancel_hold()
        active_ctrl.set_pressure(target)
        t0 = time.perf_counter()
        samples: List[Dict[str, Optional[float]]] = []
        while time.perf_counter() - t0 < SETTLE_S:
            samples.append(_read_all(ca, cb, ctrls, ali_cfg))
            time.sleep(0.5)
        final = samples[-1]
        _print_row(f'hold {SETTLE_S:.0f}s', final)

        paired_vals = [s[paired_ain] for s in samples if s.get(paired_ain) is not None]
        al_vals = [s[al_key] for s in samples if s.get(al_key) is not None]
        if paired_vals and al_vals:
            print(
                f'    paired transducer mean={statistics.mean(paired_vals):.2f} '
                f'Alicat mean={statistics.mean(al_vals):.2f} '
                f'delta={statistics.mean(paired_vals) - statistics.mean(al_vals):+.2f}'
            )

        # Swap hint: sibling transducer should stay flat
        sib_ain = 'T2/3' if paired_ain == 'T0/1' else 'T0/1'
        sib_vals = [s[sib_ain] for s in samples if s.get(sib_ain) is not None]
        if sib_vals and paired_vals:
            swing_paired = max(paired_vals) - min(paired_vals)
            swing_sib = max(sib_vals) - min(sib_vals)
            if swing_sib > 0.5 and swing_sib > swing_paired:
                print(
                    f'    ** SWAP? sibling {sib_ain} moved more ({swing_sib:.2f}) '
                    f'than paired ({swing_paired:.2f}) on {active_key} vacuum'
                )

    print(f'  -> restore atmosphere DIO{dio}')
    active_lj.set_solenoid(False)
    active_ctrl.set_pressure(SAFE_SP)
    time.sleep(4.0)
    _print_row('restored', _read_all(ca, cb, ctrls, ali_cfg))


def cleanup(ca, cb, ctrls, shared) -> None:
    ca.set_solenoid(False)
    cb.set_solenoid(False)
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
    parser = argparse.ArgumentParser(description='Vacuum pull + solenoid routing test')
    parser.add_argument('--targets', type=float, nargs='+', default=DEFAULT_TARGETS)
    args = parser.parse_args()

    config = load_config()
    ca, cb, ctrls, shared, ali_cfg = _setup(config)
    try:
        for port_key in ['port_a', 'port_b']:
            run_port_vacuum_pull(port_key, args.targets, ca, cb, ctrls, ali_cfg)
            time.sleep(3.0)
    finally:
        cleanup(ca, cb, ctrls, shared)

    print(f'\n{"="*72}')
    print('DONE — confirm vacuum pump audibly pulled on each port')
    print('If SWAP? warnings appeared, check solenoid DIO wiring vs config')
    print(f'{"="*72}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
