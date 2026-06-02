"""Measure production-like hardware poll cadence with both ports active."""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_config
from app.hardware.alicat import AlicatController, AlicatController as AC
from app.hardware.port import Port, PortId


def _build_ports(config: dict) -> tuple[Port, Port]:
    lj = config['hardware']['labjack']
    ali = config['hardware']['alicat']
    sol = config['hardware'].get('solenoid', {})

    def lj_cfg(key: str) -> dict:
        base = {k: v for k, v in lj.items() if k not in {'port_a', 'port_b'}}
        return {**base, **lj.get(key, {})}

    def ali_cfg(key: str) -> dict:
        base = {k: v for k, v in ali.items() if k not in {'port_a', 'port_b'}}
        return {**base, **ali.get(key, {})}

    pa = Port(PortId.PORT_A, lj_cfg('port_a'), ali_cfg('port_a'), sol)
    pb = Port(PortId.PORT_B, lj_cfg('port_b'), ali_cfg('port_b'), sol)
    return pa, pb


def run_benchmark(
    *,
    duration_s: float,
    poll_ms: float,
    poll_ms_precision: float,
    alicat_div_normal: int,
    alicat_div_precision: int,
    labjack_div_sibling: int,
    precision_owner: str | None,
) -> None:
    config = load_config()
    pa, pb = _build_ports(config)
    if not pa.connect() or not pb.connect():
        raise SystemExit('Hardware connect failed')

    pa.refresh_alicat()
    pb.refresh_alicat()

    owner = PortId.PORT_A if precision_owner == 'port_a' else (
        PortId.PORT_B if precision_owner == 'port_b' else None
    )
    interval_s = max(0.0, (poll_ms_precision if owner else poll_ms) / 1000.0)
    countdown = {'port_a': 0, 'port_b': 0}
    lj_countdown = {'port_a': 0, 'port_b': 0}
    last: dict[str, object] = {}
    cycles: list[float] = []
    owner_reads = 0
    sibling_reads = 0

    print('=' * 72)
    print(f'Mode: {"precision owner=" + precision_owner if owner else "dual normal"}')
    print(f'  interval_ms={poll_ms_precision if owner else poll_ms}')
    print('=' * 72)

    t_end = time.perf_counter() + duration_s
    while time.perf_counter() < t_end:
        t0 = time.perf_counter()
        for key, port, div in (
            ('port_a', pa, alicat_div_precision if owner == PortId.PORT_A else alicat_div_normal),
            ('port_b', pb, alicat_div_precision if owner == PortId.PORT_B else alicat_div_normal),
        ):
            if countdown[key] <= 0:
                port.refresh_alicat()
                countdown[key] = max(0, div - 1)
            else:
                countdown[key] -= 1

        for key, port, pid in (('port_a', pa, PortId.PORT_A), ('port_b', pb, PortId.PORT_B)):
            if owner is None:
                port.read_fast()
            elif pid == owner:
                port.read_precision_fast()
                owner_reads += 1
            else:
                if lj_countdown[key] <= 0:
                    port.read_fast()
                    sibling_reads += 1
                    lj_countdown[key] = max(0, labjack_div_sibling - 1)
                else:
                    lj_countdown[key] -= 1

        elapsed = time.perf_counter() - t0
        cycles.append(elapsed)
        sleep_s = max(0.0, interval_s - elapsed)
        if sleep_s:
            time.sleep(sleep_s)

    pa.disconnect()
    pb.disconnect()

    n = len(cycles)
    mean_ms = statistics.mean(cycles) * 1000
    print(f'Cycles={n} mean={mean_ms:.2f}ms (~{1000/mean_ms:.1f} Hz)')
    if owner:
        print(f'  Precision owner LabJack reads: {owner_reads} (~{owner_reads/duration_s:.1f}/s)')
        print(f'  Sibling LabJack reads: {sibling_reads} (~{sibling_reads/duration_s:.1f}/s)')


def main() -> int:
    parser = argparse.ArgumentParser(description='Production poll benchmark')
    parser.add_argument('--duration', type=float, default=15.0)
    parser.add_argument('--precision-owner', choices=['port_a', 'port_b'], default=None)
    args = parser.parse_args()

    config = load_config()
    timing = config.get('timing', {})
    poll_ms = float(timing.get('hardware_poll_interval_ms', 7))
    poll_ms_precision = float(timing.get('hardware_poll_interval_ms_precision', 0))
    div_normal = int(timing.get('alicat_poll_divisor_normal', 14))
    div_precision = int(timing.get('alicat_poll_divisor_precision', 2))
    lj_sibling = int(timing.get('labjack_poll_divisor_sibling', 14))

    run_benchmark(
        duration_s=args.duration,
        poll_ms=poll_ms,
        poll_ms_precision=poll_ms_precision,
        alicat_div_normal=div_normal,
        alicat_div_precision=div_precision,
        labjack_div_sibling=lj_sibling,
        precision_owner=args.precision_owner,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
