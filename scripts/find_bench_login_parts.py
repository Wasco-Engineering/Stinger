#!/usr/bin/env python
"""Find Part ID / Sequence ID (and shop orders) for bench switch validation.

Matches stand vacuum-switch behavior found on the bench:
  Port A (left):  ~10 PSIA trip on vacuum pull
  Port B (right): ~1–2 PSIA trip on vacuum pull

Usage (from repo root, venv active):
    python scripts/find_bench_login_parts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_config
from app.database.session import get_engine, initialize_database
from app.services.ptp_service import derive_test_setup, load_ptp_from_db, validate_ptp_params
from app.services.sweep_utils import resolve_sweep_mode

# Defaults aligned with bench vacuum-switch characterization (May 2026).
RECOMMENDED = {
    'port_a': ('17029', '399', 'Decreasing ~8.3 PSI gauge; vacuum sweep; QAL16 (seq 399)'),
    'port_b': ('17036', '399', 'Decreasing ~50 Torr; vacuum sweep; QAL16 (seq 399)'),
}


def _print_shop_orders(conn, part_id: str, sequence_id: str, limit: int = 5) -> None:
    from sqlalchemy import text

    rows = conn.execute(
        text(
            """
            SELECT TOP (:lim) ShopOrder, OrderQTY, OperatorID
            FROM OrderCalibrationMaster
            WHERE LTRIM(RTRIM(PartID)) = :part
            ORDER BY ShopOrder DESC
            """
        ),
        {'part': part_id.strip(), 'lim': limit},
    ).fetchall()
    if not rows:
        print(f'    (no rows in OrderCalibrationMaster for PartID={part_id})')
        return
    for row in rows:
        print(
            f'    Shop order {str(row[0]).strip():<20} qty={row[1]}  '
            f'(use this in Stinger login; sequence {sequence_id} from PTP)'
        )


def _verify_ptp(part_id: str, sequence_id: str) -> None:
    params = load_ptp_from_db(part_id, sequence_id)
    if not params:
        print('    PTP: not found in database')
        return
    ok, errors = validate_ptp_params(params)
    if not ok:
        print(f'    PTP: invalid ({errors})')
        return
    setup = derive_test_setup(part_id, sequence_id, params)
    mode = resolve_sweep_mode(setup, 14.7)
    print(
        f'    PTP: OK — direction={setup.activation_direction}, '
        f'target={setup.activation_target} {setup.units_label}, '
        f'ref={setup.pressure_reference}, sweep={mode}'
    )


def main() -> int:
    print('=' * 72)
    print('RECOMMENDED LOGIN (manual Part ID + Sequence if no shop order)')
    print('=' * 72)
    for port, (part, seq, note) in RECOMMENDED.items():
        label = port.replace('_', ' ').title()
        print(f'\n{label}:')
        print(f'  Part ID:     {part}')
        print(f'  Sequence ID: {seq}')
        print(f'  Notes:       {note}')

    config = load_config()
    if not initialize_database(config.get('database', {})):
        print('\nDatabase unavailable — use Part/Sequence above with Manual Entry at login.')
        return 0

    engine = get_engine()
    if engine is None:
        print('\nDatabase engine unavailable.')
        return 1

    print('\n' + '=' * 72)
    print('SHOP ORDERS (MAX / OrderCalibrationMaster)')
    print('=' * 72)
    with engine.connect() as conn:
        for port, (part, seq, _) in RECOMMENDED.items():
            print(f'\n{port.replace("_", " ").title()} — Part {part}, Seq {seq}:')
            _print_shop_orders(conn, part, seq)
            _verify_ptp(part, seq)

    print('\n' + '=' * 72)
    print('In Stinger: login → if shop order not found, use Manual Entry with Part + Sequence.')
    print('Sequence 399 → QAL16 (Test → cycle → precision). Sequence 300 → QAL15 (Pressurize path).')
    print('=' * 72)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
