#!/usr/bin/env python3
"""Convert mensor_triplet_correlation CSV to pressure alignment optimizer input."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def convert(input_csv: Path, output_csv: Path, port_id: str) -> int:
    with input_csv.open('r', newline='', encoding='utf-8') as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise ValueError(f'No rows in {input_csv}')

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        'timestamp',
        'port_id',
        'phase',
        'target_abs_psi',
        'transducer_abs_psi',
        'transducer_raw_abs_psi',
        'alicat_abs_psi',
        'mensor_abs_psia',
    ]
    with output_csv.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for idx, row in enumerate(rows):
            target = row.get('target_psia') or row.get('target_abs_psi')
            phase = f"static_{int(round(float(target)))}" if target else 'static_unknown'
            writer.writerow(
                {
                    'timestamp': str(idx),
                    'port_id': port_id,
                    'phase': phase,
                    'target_abs_psi': target,
                    'transducer_abs_psi': row.get('transducer_psia') or row.get('transducer_abs_psi'),
                    'transducer_raw_abs_psi': row.get('transducer_psia') or row.get('transducer_abs_psi'),
                    'alicat_abs_psi': row.get('alicat_psia') or row.get('alicat_abs_psi'),
                    'mensor_abs_psia': row.get('mensor_psia') or row.get('mensor_abs_psia'),
                }
            )
    print(f'Wrote {len(rows)} rows to {output_csv}')
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description='Convert triplet CSV to alignment format.')
    parser.add_argument('--input-csv', required=True)
    parser.add_argument('--output-csv', required=True)
    parser.add_argument('--port', default='port_b')
    args = parser.parse_args()
    convert(Path(args.input_csv), Path(args.output_csv), args.port.strip().lower())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
