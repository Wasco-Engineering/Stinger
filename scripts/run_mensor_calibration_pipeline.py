#!/usr/bin/env python3
"""Run Mensor-referenced calibration fit and verification from alignment or triplet CSV.

Typical hardware workflow (port_b, 0–20 PSIA):

  1. Collect data (vacuum for targets below ~12 PSIA):
     .\\.venv\\Scripts\\python.exe scripts\\pressure_alignment_scan.py ^
       --with-mensor --mode static --min-abs 0 --max-abs 20 ^
       --static-points 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20 ^
       --static-hold-s 10 --sample-hz 5 --settle-tolerance-psi 0.05 ^
       --capture-raw-profile --ports port_b ^
       --output-csv scripts/data/mensor_alignment_port_b.csv

  2. Fit models and write recommended_calibration.yaml:
     .\\.venv\\Scripts\\python.exe scripts\\run_mensor_calibration_pipeline.py ^
       --input-csv scripts/data/mensor_alignment_port_b.csv --port port_b

  Or from triplet correlation output:
     .\\.venv\\Scripts\\python.exe scripts\\run_mensor_calibration_pipeline.py ^
       --triplet-csv scripts/data/mensor_triplet_port_b_*.csv --port port_b
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description='Fit Mensor-referenced models and verify p99 <= 1 Torr.')
    parser.add_argument('--input-csv', help='Alignment CSV (with mensor_abs_psia).')
    parser.add_argument('--triplet-csv', help='Triplet CSV from mensor_triplet_correlation.py.')
    parser.add_argument('--port', default='port_b')
    parser.add_argument('--output-dir', default='scripts/data/mensor_cal_out')
    parser.add_argument('--fit-max-psi', type=float, default=20.0)
    parser.add_argument('--pass-threshold-torr', type=float, default=1.0)
    args = parser.parse_args()

    if not args.input_csv and not args.triplet_csv:
        parser.error('Provide --input-csv or --triplet-csv')

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    alignment_csv = args.input_csv
    temp_path: Path | None = None
    if args.triplet_csv:
        from scripts.triplet_to_alignment_csv import convert

        temp_path = Path(tempfile.mkstemp(suffix='_alignment.csv')[1])
        convert(Path(args.triplet_csv), temp_path, args.port)
        alignment_csv = str(temp_path)

    opt_script = PROJECT_ROOT / 'scripts' / 'optimize_pressure_calibration.py'
    cmd = [
        sys.executable,
        str(opt_script),
        '--input-csv',
        alignment_csv,
        '--ports',
        args.port,
        '--output-dir',
        str(output_dir),
        '--reference',
        'mensor',
        '--sensor',
        'both',
        '--fit-max-psi',
        str(args.fit_max_psi),
        '--pass-threshold-torr',
        str(args.pass_threshold_torr),
        '--min-near-target-samples',
        '30',
    ]
    print('Running:', ' '.join(cmd))
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, check=False)
    if temp_path is not None:
        temp_path.unlink(missing_ok=True)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
