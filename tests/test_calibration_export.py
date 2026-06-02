"""Tests for quality-cal calibration export."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.services.pressure_calibration import ONE_TORR_PSI, apply_error_model
from quality_cal.core.calibration_export import (
    build_recommended_config,
    merge_hardware_into_stinger_config,
    point_passes_mensor_tolerance,
)
from quality_cal.session import CalibrationPointResult, PortCalibrationResult, QualityCalibrationSession


def _point(target: float, mensor: float, alicat: float, transducer: float, index: int = 1) -> CalibrationPointResult:
    deviation = mensor - alicat
    return CalibrationPointResult(
        port_id='port_b',
        point_index=index,
        point_total=21,
        target_psia=target,
        route='pressure',
        mensor_psia=mensor,
        alicat_psia=alicat,
        transducer_psia=transducer,
        deviation_psia=deviation,
        passed=abs(deviation) <= ONE_TORR_PSI,
        settle_duration_s=10.0,
        hold_duration_s=8.0,
        sample_count=32,
    )


def test_build_recommended_config_from_session_points() -> None:
    session = QualityCalibrationSession()
    points = []
    for idx, target in enumerate(range(1, 22)):
        err = 0.01 * target + 0.03
        points.append(
            _point(
                float(target),
                mensor=float(target),
                alicat=float(target) - 0.05,
                transducer=float(target) + err,
                index=idx + 1,
            )
        )
    session.right_port = PortCalibrationResult(port_id='port_b', points=points)

    snippet = build_recommended_config(session, fit_max_psi=20.0)
    assert 'hardware' in snippet
    assert 'transducer_error_model' in snippet['hardware']['labjack']['port_b']
    assert 'alicat_error_model' in snippet['hardware']['alicat']['port_b']

    model = snippet['hardware']['labjack']['port_b']['transducer_error_model']
    corrected = apply_error_model(points[10].transducer_psia, model)
    assert abs(corrected - points[10].mensor_psia) < 0.05


def test_merge_hardware_into_stinger_config(tmp_path: Path) -> None:
    stinger = {
        'hardware': {
            'labjack': {'port_a': {'transducer_ain': 2}, 'port_b': {}},
            'alicat': {'port_b': {'address': 'B'}},
            'measurement': {'transducer_only_below_psi': 10.0},
        }
    }
    snippet = {
        'hardware': {
            'labjack': {
                'pressure_filter_alpha': 0.0,
                'port_b': {'transducer_error_model': {'type': 'quadratic', 'c_error_psi': 0.1}},
            },
            'alicat': {'port_b': {'alicat_error_model': {'type': 'quadratic', 'c_error_psi': 0.2}}},
        }
    }
    merged = merge_hardware_into_stinger_config(stinger, snippet)
    assert merged['hardware']['measurement']['transducer_only_below_psi'] == 20.0
    assert 'transducer_error_model' in merged['hardware']['labjack']['port_b']

    out = tmp_path / 'stinger.yaml'
    out.write_text(yaml.safe_dump(merged), encoding='utf-8')
    loaded = yaml.safe_load(out.read_text(encoding='utf-8'))
    assert loaded['hardware']['labjack']['port_a']['transducer_ain'] == 2


def test_point_passes_mensor_tolerance() -> None:
    ok = _point(10.0, 10.0, 10.0, 10.1)
    assert point_passes_mensor_tolerance(ok)
    bad = _point(10.0, 10.0, 10.1, 10.1)
    assert not point_passes_mensor_tolerance(bad)
