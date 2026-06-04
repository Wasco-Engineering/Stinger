"""Quick NO/NC poll for bench bring-up. Usage: python scripts/poll_switch_state.py"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_port_config, load_config
from app.hardware.labjack import LabJackController


def main() -> None:
    cfg = load_config()
    for pk in ('port_a', 'port_b'):
        pc = get_port_config(cfg, pk)
        lj_cfg = {**cfg['hardware']['labjack'], **pc['labjack']}
        c = LabJackController(lj_cfg)
        if not c.configure():
            print(f'{pk}: configure failed: {c._last_status}')
            continue
        c.configure_di_pins(
            lj_cfg['switch_no_dio'],
            lj_cfg['switch_nc_dio'],
            lj_cfg.get('switch_com_dio'),
            com_state=lj_cfg.get('switch_com_state', 0),
        )
        s = c.read_switch_state()
        print(
            f'{pk}: NO=DIO{lj_cfg["switch_no_dio"]} NC=DIO{lj_cfg["switch_nc_dio"]} '
            f'COM=DIO{lj_cfg.get("switch_com_dio")} -> '
            f'no_active={getattr(s, "no_active", None)} '
            f'nc_active={getattr(s, "nc_active", None)} '
            f'valid={getattr(s, "is_valid", None) if s else None}'
        )
        c.cleanup()


if __name__ == '__main__':
    main()
