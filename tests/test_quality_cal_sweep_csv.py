"""Tests for calibration sweep CSV writer."""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import pytest

from quality_cal.core.calibration_runner import SWEEP_CSV_COLUMNS, _SweepCsvWriter


def test_sweep_csv_writer_columns() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'sweep.csv'
        writer = _SweepCsvWriter(path, 'port_b')
        writer.write_row(
            phase='static_10',
            target_abs_psi=10.0,
            transducer_abs_psi=10.1,
            alicat_abs_psi=10.05,
            mensor_abs_psia=10.0,
        )
        writer.close()
        with path.open(encoding='utf-8') as handle:
            reader = csv.DictReader(handle)
            assert reader.fieldnames == SWEEP_CSV_COLUMNS
            row = next(reader)
            assert row['phase'] == 'static_10'
            assert float(row['mensor_abs_psia']) == pytest.approx(10.0)
