"""
Verify switch configuration via application-level LabJackController.

Confirms that the LabJackController.read_switch_state() method returns
correct NO/NC/activated states through a pressure sweep on each port.

Usage (from repo root, venv active):
    python scripts/verify_switch_config.py
    python scripts/verify_switch_config.py --port port_a --sweep-vacuum --start-psi 14 --end-psi 0.5
    python scripts/verify_switch_config.py --port port_a --start-psi 10 --end-psi 28  # positive-pressure switch
    python scripts/verify_switch_config.py --port port_b --sweep-vacuum  # only if right port wired
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_port_config, is_port_installed, load_config
from app.hardware.alicat import AlicatController
from app.hardware.labjack import LabJackController


GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
RESET = '\033[0m'
BOLD = '\033[1m'


def _sweep_reached_target(
    *,
    to_vacuum: bool,
    end_psi: float,
    alicat_psi: float,
    trans_psi: Optional[float],
    tolerance_psi: float = 1.5,
) -> bool:
    """Use transducer when available; Alicat often lags on vacuum pulls."""
    measured = trans_psi if trans_psi is not None else alicat_psi
    if to_vacuum:
        return measured <= end_psi + tolerance_psi
    return abs(measured - end_psi) < tolerance_psi


def verify_port(
    port_key: str,
    config: dict,
    start_psi: float,
    end_psi: float,
    rate_psi_s: float,
    to_vacuum: bool,
) -> bool:
    """Run a pressure sweep and verify switch detection via LabJackController."""

    port_cfg = get_port_config(config, port_key)
    lj_base = config['hardware']['labjack']
    lj_port = port_cfg['labjack']
    lj_cfg = {**lj_base, **lj_port}
    alicat_cfg = {**config['hardware']['alicat'], **port_cfg['alicat']}

    port_label = port_key.replace('_', ' ').title()
    no_dio = lj_port.get('switch_no_dio')
    nc_dio = lj_port.get('switch_nc_dio')
    com_dio = lj_port.get('switch_com_dio')

    print(f'\n{"=" * 60}')
    print(f'  {port_label} Switch Verification')
    print(f'  NO=DIO{no_dio}  NC=DIO{nc_dio}  COM=DIO{com_dio}')
    route = 'vacuum' if to_vacuum else 'atmosphere'
    print(f'  Sweep: {start_psi:.1f} -> {end_psi:.1f} PSI at {rate_psi_s:.1f} PSI/s ({route})')
    print(f'{"=" * 60}')

    # Set up LabJack
    labjack = LabJackController(lj_cfg)
    if not labjack.configure():
        print(f'  {RED}[FAIL] LabJack configure failed: {labjack._last_status}{RESET}')
        return False

    # Configure DI pins (COM as output, NO/NC as inputs)
    labjack.configure_di_pins(
        no_pin=no_dio,
        nc_pin=nc_dio,
        com_pin=com_dio,
        com_state=lj_port.get('switch_com_state', 0),
    )

    # Set solenoid
    labjack.set_solenoid(to_vacuum=to_vacuum)

    # Read initial switch state
    initial = labjack.read_switch_state()
    if initial is None:
        print(f'  {RED}[FAIL] Cannot read switch state{RESET}')
        labjack.cleanup()
        return False

    print(f'  Initial: NO_active={initial.no_active} NC_active={initial.nc_active} '
          f'activated={initial.switch_activated} valid={initial.is_valid}')

    # Set up Alicat
    alicat = AlicatController(alicat_cfg)
    if not alicat.connect():
        print(f'  {RED}[FAIL] Alicat connect failed: {alicat._last_status}{RESET}')
        labjack.cleanup()
        return False

    edges_found = []
    last_activated: Optional[bool] = None

    try:
        # Move to start
        alicat.cancel_hold()
        time.sleep(0.1)
        alicat.set_ramp_rate(0, time_unit='s')
        time.sleep(0.1)
        alicat.set_pressure(start_psi)
        time.sleep(5.0)

        # Verify at start
        switch = labjack.read_switch_state()
        trans = labjack.read_transducer()
        if switch:
            last_activated = switch.switch_activated
            print(f'\n  At start ({trans.pressure:.1f} PSI): '
                  f'NO={switch.no_active} NC={switch.nc_active} '
                  f'activated={switch.switch_activated} valid={switch.is_valid}')

        # Sweep
        alicat.set_ramp_rate(rate_psi_s, time_unit='s')
        time.sleep(0.1)
        alicat.set_pressure(end_psi)

        pressure_delta = abs(end_psi - start_psi)
        expected_duration = pressure_delta / rate_psi_s if rate_psi_s > 0 else 30.0
        timeout = expected_duration + (45.0 if to_vacuum else 15.0)
        sweep_start = time.perf_counter()
        last_alicat_time = 0.0
        last_alicat_p = start_psi

        print(f'\n  Sweeping...')

        while time.perf_counter() - sweep_start < timeout:
            now = time.perf_counter()

            if now - last_alicat_time >= 0.2:
                status = alicat.read_status()
                if status:
                    last_alicat_p = status.pressure
                last_alicat_time = now

            switch = labjack.read_switch_state()
            trans = labjack.read_transducer()

            if switch and last_activated is not None:
                if switch.switch_activated != last_activated:
                    edge_pressure = trans.pressure if trans else last_alicat_p
                    direction = 'ACTIVATED' if switch.switch_activated else 'DEACTIVATED'
                    edges_found.append((direction, edge_pressure, last_alicat_p))
                    print(f'  {GREEN}[EDGE]{RESET} Switch {direction} at '
                          f'trans={edge_pressure:.2f} PSI, alicat={last_alicat_p:.2f} PSI')

            if switch:
                last_activated = switch.switch_activated

            trans_p = trans.pressure if trans else None
            if _sweep_reached_target(
                to_vacuum=to_vacuum,
                end_psi=end_psi,
                alicat_psi=last_alicat_p,
                trans_psi=trans_p,
            ):
                break

            time.sleep(0.02)

        # Hold at end
        time.sleep(2.0)
        switch = labjack.read_switch_state()
        trans = labjack.read_transducer()
        if switch:
            print(f'\n  At end ({trans.pressure:.1f} PSI): '
                  f'NO={switch.no_active} NC={switch.nc_active} '
                  f'activated={switch.switch_activated} valid={switch.is_valid}')

        # Return
        print(f'\n  Returning to {start_psi:.1f} PSI...')
        alicat.set_pressure(start_psi)
        return_start = time.perf_counter()
        while time.perf_counter() - return_start < timeout:
            now = time.perf_counter()
            if now - last_alicat_time >= 0.2:
                status = alicat.read_status()
                if status:
                    last_alicat_p = status.pressure
                last_alicat_time = now

            switch = labjack.read_switch_state()
            trans = labjack.read_transducer()
            if switch and last_activated is not None:
                if switch.switch_activated != last_activated:
                    edge_pressure = trans.pressure if trans else last_alicat_p
                    direction = 'ACTIVATED' if switch.switch_activated else 'DEACTIVATED'
                    edges_found.append((direction, edge_pressure, last_alicat_p))
                    print(f'  {GREEN}[EDGE]{RESET} Switch {direction} at '
                          f'trans={edge_pressure:.2f} PSI, alicat={last_alicat_p:.2f} PSI')
            if switch:
                last_activated = switch.switch_activated
            trans = labjack.read_transducer()
            trans_p = trans.pressure if trans else None
            if _sweep_reached_target(
                to_vacuum=False,
                end_psi=start_psi,
                alicat_psi=last_alicat_p,
                trans_psi=trans_p,
                tolerance_psi=2.0,
            ):
                break
            time.sleep(0.02)

        time.sleep(2.0)
        switch = labjack.read_switch_state()
        trans = labjack.read_transducer()
        if switch:
            print(f'\n  At rest ({trans.pressure:.1f} PSI): '
                  f'NO={switch.no_active} NC={switch.nc_active} '
                  f'activated={switch.switch_activated} valid={switch.is_valid}')

    finally:
        try:
            alicat.set_ramp_rate(0, time_unit='s')
            alicat.set_pressure(start_psi)
        except Exception:
            pass
        try:
            alicat.disconnect()
        except Exception:
            pass
        labjack.set_solenoid_safe()
        labjack.cleanup()

    # Summary
    print(f'\n  {"-" * 50}')
    if len(edges_found) >= 2:
        print(f'  {GREEN}{BOLD}[PASS]{RESET} {port_label}: {len(edges_found)} edges detected')
        for direction, trans_p, alicat_p in edges_found:
            print(f'    {direction} at trans={trans_p:.2f} PSI, alicat={alicat_p:.2f} PSI')
        return True
    elif len(edges_found) == 1:
        print(f'  {YELLOW}[WARN]{RESET} {port_label}: Only 1 edge detected (expected 2)')
        return True
    else:
        print(f'  {RED}[FAIL]{RESET} {port_label}: No edges detected during sweep!')
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description='Verify switch config via app-level controller')
    parser.add_argument(
        '--port',
        choices=['port_a', 'port_b'],
        default='port_a',
        help='Port to test (default port_a / left only)',
    )
    parser.add_argument(
        '--both',
        action='store_true',
        help='Test every installed port (skips port_installed: false)',
    )
    parser.add_argument('--sweep-vacuum', action='store_true', help='Route solenoid to vacuum')
    parser.add_argument('--start-psi', type=float, default=None)
    parser.add_argument('--end-psi', type=float, default=None)
    parser.add_argument('--rate', type=float, default=1.5, help='PSI/s ramp rate')
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
    config = load_config()

    results = {}

    ports_to_run: list[str] = []
    if args.both:
        for key in ('port_a', 'port_b'):
            if is_port_installed(config, key):
                ports_to_run.append(key)
    else:
        ports_to_run.append(args.port)

    for port_key in ports_to_run:
        if not is_port_installed(config, port_key):
            print(f'{YELLOW}Skipping {port_key}: port_installed=false in config{RESET}')
            continue
        start = args.start_psi if args.start_psi is not None else 14.7
        if port_key == 'port_b':
            end = args.end_psi if args.end_psi is not None else 2.0
            # Port B defaults to vacuum sweep unless --sweep-vacuum omitted with custom range
            to_vacuum = args.sweep_vacuum or (
                args.start_psi is None and args.end_psi is None
            )
        else:
            end = args.end_psi if args.end_psi is not None else 30.0
            to_vacuum = args.sweep_vacuum
        results[port_key] = verify_port(
            port_key,
            config,
            start_psi=start,
            end_psi=end,
            rate_psi_s=args.rate,
            to_vacuum=to_vacuum,
        )

    print(f'\n{"=" * 60}')
    print(f'  FINAL RESULTS')
    print(f'{"=" * 60}')
    for port, passed in results.items():
        label = port.replace('_', ' ').title()
        status = f'{GREEN}PASS{RESET}' if passed else f'{RED}FAIL{RESET}'
        print(f'  {label}: [{status}]')

    return 0 if all(results.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
