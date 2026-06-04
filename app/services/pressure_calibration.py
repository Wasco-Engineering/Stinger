"""Pressure calibration helpers for offline fitting and runtime correction."""

from __future__ import annotations

import itertools
import math
import statistics
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple

import numpy as np

TORR_PER_PSI = 51.71493256
ONE_TORR_PSI = 1.0 / TORR_PER_PSI

REFERENCE_ALICAT = 'alicat'
REFERENCE_MENSOR = 'mensor'
SENSOR_TRANSDUCER = 'transducer'
SENSOR_ALICAT = 'alicat'

ReferenceKind = Literal['alicat', 'mensor']
SensorKind = Literal['transducer', 'alicat']

REQUIRED_ALIGNMENT_COLUMNS = {
    'timestamp',
    'port_id',
    'phase',
    'target_abs_psi',
    'transducer_abs_psi',
    'alicat_abs_psi',
}

OPTIONAL_ALIGNMENT_COLUMNS = {
    'mensor_abs_psia',
    'transducer_raw_abs_psi',
}


@dataclass
class CalibrationSample:
    """Single alignment sample used by calibration fitting/scoring."""

    index: int
    timestamp: float
    port_id: str
    phase: str
    target_abs_psi: Optional[float]
    transducer_abs_psi: Optional[float]
    alicat_abs_psi: Optional[float]
    mensor_abs_psia: Optional[float] = None


def psi_to_torr(psi: float) -> float:
    """Convert PSI to Torr."""
    return psi * TORR_PER_PSI


def torr_to_psi(torr: float) -> float:
    """Convert Torr to PSI."""
    return torr / TORR_PER_PSI


def _is_static_phase(phase: str) -> bool:
    return phase.startswith('static_')


def _reference_pressure(sample: CalibrationSample, reference: ReferenceKind) -> Optional[float]:
    if reference == REFERENCE_MENSOR:
        return sample.mensor_abs_psia
    return sample.alicat_abs_psi


def _sensor_pressure(sample: CalibrationSample, sensor: SensorKind) -> Optional[float]:
    if sensor == SENSOR_ALICAT:
        return sample.alicat_abs_psi
    return sample.transducer_abs_psi


def filter_samples_pressure_band(
    samples: Sequence[CalibrationSample],
    *,
    min_psi: float = 0.0,
    max_psi: float,
    reference: ReferenceKind = REFERENCE_MENSOR,
) -> List[CalibrationSample]:
    """Keep samples whose reference pressure lies in [min_psi, max_psi]."""
    filtered: List[CalibrationSample] = []
    for sample in samples:
        ref = _reference_pressure(sample, reference)
        if ref is None:
            continue
        if min_psi <= ref <= max_psi:
            filtered.append(sample)
    return filtered


def select_near_target_samples(
    samples: Sequence[CalibrationSample],
    *,
    tolerance_psi: float = 0.2,
    static_only: bool = True,
    reference: ReferenceKind = REFERENCE_ALICAT,
) -> List[CalibrationSample]:
    """Select samples where the reference sensor is near commanded target pressure.

    Rule:
    - target_abs_psi and reference pressure must be present
    - |reference - target_abs_psi| <= tolerance_psi
    - optionally restrict to static phases only
    """
    selected: List[CalibrationSample] = []
    for sample in samples:
        if static_only and not _is_static_phase(sample.phase):
            continue
        ref = _reference_pressure(sample, reference)
        if sample.target_abs_psi is None or ref is None:
            continue
        if abs(ref - sample.target_abs_psi) <= tolerance_psi:
            selected.append(sample)
    return selected


def split_train_validation(
    samples: Sequence[CalibrationSample],
    *,
    holdout_stride: int = 5,
) -> Tuple[List[CalibrationSample], List[CalibrationSample]]:
    """Deterministic split by sample index for reproducible holdout."""
    if holdout_stride < 2:
        raise ValueError('holdout_stride must be >= 2')
    train: List[CalibrationSample] = []
    validation: List[CalibrationSample] = []
    for i, sample in enumerate(samples):
        if i % holdout_stride == 0:
            validation.append(sample)
        else:
            train.append(sample)
    return train, validation


def _quantile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError('Cannot compute quantile of empty values')
    if q <= 0:
        return min(values)
    if q >= 1:
        return max(values)
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    frac = pos - lo
    return s[lo] * (1.0 - frac) + s[hi] * frac


def _linear_fit(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float]:
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError('Need at least two samples for linear fit')
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    if ss_xx <= 0:
        return 0.0, mean_y
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _percentile_candidates(segment_count: int) -> List[Tuple[float, ...]]:
    if segment_count == 3:
        grid = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]
        return list(itertools.combinations(grid, 2))
    if segment_count == 5:
        grid = [0.08, 0.16, 0.24, 0.32, 0.40, 0.52, 0.64, 0.76, 0.88]
        return list(itertools.combinations(grid, 4))
    raise ValueError(f'Unsupported segment_count={segment_count}')


def _fit_piecewise_for_breakpoints(
    xs: Sequence[float],
    ys: Sequence[float],
    breakpoints: Sequence[float],
    *,
    min_segment_size: int = 20,
) -> Optional[List[Tuple[float, float]]]:
    lines: List[Tuple[float, float]] = []
    lower = -float('inf')
    for upper in list(breakpoints) + [float('inf')]:
        seg_x = [x for x in xs if lower <= x < upper]
        seg_y = [y for x, y in zip(xs, ys) if lower <= x < upper]
        if len(seg_x) < min_segment_size:
            return None
        slope, intercept = _linear_fit(seg_x, seg_y)
        lines.append((slope, intercept))
        lower = upper
    return lines


def _pressure_axis_value(
    sample: CalibrationSample,
    *,
    measured: float,
    pressure_axis: Literal['measured', 'target'],
) -> float:
    if pressure_axis == 'target' and sample.target_abs_psi is not None:
        return float(sample.target_abs_psi)
    return float(measured)


def _extract_fit_pairs(
    samples: Sequence[CalibrationSample],
    *,
    sensor: SensorKind,
    reference: ReferenceKind,
    pressure_axis: Literal['measured', 'target'] = 'measured',
) -> Tuple[List[float], List[float]]:
    xs: List[float] = []
    ys: List[float] = []
    for sample in samples:
        measured = _sensor_pressure(sample, sensor)
        ref = _reference_pressure(sample, reference)
        if measured is None or ref is None:
            continue
        xs.append(_pressure_axis_value(sample, measured=float(measured), pressure_axis=pressure_axis))
        ys.append(float(measured) - float(ref))
    return xs, ys


def evaluate_error_model(
    pressure_psi: float,
    model: Optional[Dict[str, Any]],
    *,
    target_psi: Optional[float] = None,
) -> float:
    """Return modeled sensor error(psi) at the given pressure."""
    if not model:
        return 0.0
    axis = str(model.get('pressure_axis', 'measured')).strip().lower()
    lookup_psi = float(target_psi) if axis == 'target' and target_psi is not None else float(pressure_psi)
    model_type = str(model.get('type', '')).strip().lower()
    if model_type == 'piecewise_linear':
        segments = model.get('segments', [])
        if not isinstance(segments, list) or not segments:
            return 0.0
        for segment in segments:
            max_psi = segment.get('max_psi')
            if max_psi is not None and lookup_psi >= float(max_psi):
                continue
            slope = float(segment.get('slope_error_per_psi', 0.0))
            intercept = float(segment.get('intercept_error_psi', 0.0))
            return slope * lookup_psi + intercept
        last = segments[-1]
        slope = float(last.get('slope_error_per_psi', 0.0))
        intercept = float(last.get('intercept_error_psi', 0.0))
        return slope * lookup_psi + intercept
    if model_type == 'quadratic':
        a = float(model.get('a_error_per_psi2', 0.0))
        b = float(model.get('b_error_per_psi', 0.0))
        c = float(model.get('c_error_psi', 0.0))
        return a * lookup_psi * lookup_psi + b * lookup_psi + c
    return 0.0


def apply_error_model(
    pressure_psi: float,
    model: Optional[Dict[str, Any]],
    *,
    target_psi: Optional[float] = None,
) -> float:
    """Apply error model as corrected = measured - modeled_error."""
    return pressure_psi - evaluate_error_model(pressure_psi, model, target_psi=target_psi)


def build_legacy_two_band_model(
    *,
    breakpoint_psi: float,
    low_slope_error_per_psi: float,
    low_intercept_error_psi: float,
    high_slope_error_per_psi: float,
    high_intercept_error_psi: float,
) -> Dict[str, Any]:
    """Convert existing two-band config fields to generic piecewise config."""
    return {
        'type': 'piecewise_linear',
        'segments': [
            {
                'max_psi': float(breakpoint_psi),
                'slope_error_per_psi': float(low_slope_error_per_psi),
                'intercept_error_psi': float(low_intercept_error_psi),
            },
            {
                'max_psi': None,
                'slope_error_per_psi': float(high_slope_error_per_psi),
                'intercept_error_psi': float(high_intercept_error_psi),
            },
        ],
    }


def replay_corrected_series(
    measured_pressures_psi: Sequence[float],
    *,
    model: Optional[Dict[str, Any]],
    ema_alpha: float,
) -> List[float]:
    """Replay correction + optional EMA over a pressure series."""
    corrected: List[float] = []
    ema_value: Optional[float] = None
    alpha = float(ema_alpha)
    for raw in measured_pressures_psi:
        adjusted = apply_error_model(float(raw), model)
        if alpha <= 0.0 or alpha >= 1.0:
            ema_value = adjusted
        elif ema_value is None:
            ema_value = adjusted
        else:
            ema_value = alpha * adjusted + (1.0 - alpha) * ema_value
        corrected.append(float(ema_value))
    return corrected


def fit_piecewise_linear_error_model(
    train_samples: Sequence[CalibrationSample],
    *,
    segment_count: int,
    min_segment_size: int = 20,
    sensor: SensorKind = SENSOR_TRANSDUCER,
    reference: ReferenceKind = REFERENCE_ALICAT,
    pressure_axis: Literal['measured', 'target'] = 'measured',
) -> Dict[str, Any]:
    """Fit piecewise-linear model for error vs measured pressure."""
    if segment_count not in {3, 5}:
        raise ValueError('segment_count must be 3 or 5')
    xs, ys = _extract_fit_pairs(
        train_samples,
        sensor=sensor,
        reference=reference,
        pressure_axis=pressure_axis,
    )
    if len(xs) < min_segment_size * segment_count:
        raise ValueError('Not enough training samples for requested segment count')

    breakpoint_quantiles = _percentile_candidates(segment_count)
    best_model: Optional[Dict[str, Any]] = None
    best_mae = float('inf')
    for q_tuple in breakpoint_quantiles:
        breakpoints = [_quantile(xs, q) for q in q_tuple]
        if any(b2 <= b1 for b1, b2 in zip(breakpoints, breakpoints[1:])):
            continue
        lines = _fit_piecewise_for_breakpoints(xs, ys, breakpoints, min_segment_size=min_segment_size)
        if lines is None:
            continue

        segments = []
        for i, (slope, intercept) in enumerate(lines):
            segments.append(
                {
                    'max_psi': (breakpoints[i] if i < len(breakpoints) else None),
                    'slope_error_per_psi': slope,
                    'intercept_error_psi': intercept,
                }
            )
        model = {
            'type': 'piecewise_linear',
            'segments': segments,
            'pressure_axis': pressure_axis,
        }
        true_refs = [x - y for x, y in zip(xs, ys)]
        residuals = [
            abs(apply_error_model(x, model, target_psi=x if pressure_axis == 'target' else None) - ref)
            for x, ref in zip(xs, true_refs)
        ]
        mae = statistics.fmean(residuals)
        if mae < best_mae:
            best_mae = mae
            best_model = model

    if best_model is None:
        raise ValueError('Unable to fit piecewise-linear model with current constraints')
    return best_model


def fit_quadratic_error_model(
    train_samples: Sequence[CalibrationSample],
    *,
    sensor: SensorKind = SENSOR_TRANSDUCER,
    reference: ReferenceKind = REFERENCE_ALICAT,
    pressure_axis: Literal['measured', 'target'] = 'measured',
) -> Dict[str, Any]:
    """Fit quadratic error model for error vs measured pressure."""
    xs, ys = _extract_fit_pairs(
        train_samples,
        sensor=sensor,
        reference=reference,
        pressure_axis=pressure_axis,
    )
    if len(xs) < 3:
        raise ValueError('Need at least 3 samples to fit quadratic model')
    coeff = np.polyfit(np.array(xs, dtype=float), np.array(ys, dtype=float), deg=2)
    a, b, c = coeff
    return {
        'type': 'quadratic',
        'a_error_per_psi2': float(a),
        'b_error_per_psi': float(b),
        'c_error_psi': float(c),
        'pressure_axis': pressure_axis,
    }


def _quantile_abs(values: Sequence[float], q: float) -> float:
    if not values:
        return float('nan')
    return _quantile([abs(v) for v in values], q)


def score_error_series_torr(errors_psi: Sequence[float]) -> Dict[str, float]:
    """Compute absolute error metrics in Torr from psi errors."""
    if not errors_psi:
        return {
            'n': 0,
            'mean_abs_torr': float('nan'),
            'p95_abs_torr': float('nan'),
            'p99_abs_torr': float('nan'),
            'max_abs_torr': float('nan'),
        }
    abs_torr = [psi_to_torr(abs(e)) for e in errors_psi]
    return {
        'n': float(len(errors_psi)),
        'mean_abs_torr': float(statistics.fmean(abs_torr)),
        'p95_abs_torr': float(_quantile(abs_torr, 0.95)),
        'p99_abs_torr': float(_quantile(abs_torr, 0.99)),
        'max_abs_torr': float(max(abs_torr)),
    }


def score_replay(
    samples: Sequence[CalibrationSample],
    *,
    model: Optional[Dict[str, Any]],
    ema_alpha: float,
    include_mask: Optional[Sequence[bool]] = None,
    sensor: SensorKind = SENSOR_TRANSDUCER,
    reference: ReferenceKind = REFERENCE_ALICAT,
) -> Dict[str, float]:
    """Replay a model over ordered samples and score selected points vs reference."""
    measured: List[float] = []
    reference_vals: List[float] = []
    for sample in samples:
        m = _sensor_pressure(sample, sensor)
        r = _reference_pressure(sample, reference)
        if m is None or r is None:
            measured.append(float('nan'))
            reference_vals.append(float('nan'))
        else:
            measured.append(float(m))
            reference_vals.append(float(r))

    replayed: List[float] = []
    ema_value: Optional[float] = None
    alpha = float(ema_alpha)
    last_target: Optional[float] = None
    for sample, raw, ref in zip(samples, measured, reference_vals):
        if sample.target_abs_psi is not None and (
            last_target is None or abs(float(sample.target_abs_psi) - last_target) > 1e-6
        ):
            ema_value = None
            last_target = float(sample.target_abs_psi)
        target_psi = float(sample.target_abs_psi) if sample.target_abs_psi is not None else None
        adjusted = (
            apply_error_model(float(raw), model, target_psi=target_psi)
            if math.isfinite(raw)
            else float('nan')
        )
        if not math.isfinite(adjusted):
            replayed.append(float('nan'))
            continue
        if alpha <= 0.0 or alpha >= 1.0:
            ema_value = adjusted
        elif ema_value is None:
            ema_value = adjusted
        else:
            ema_value = alpha * adjusted + (1.0 - alpha) * ema_value
        replayed.append(float(ema_value))
    if include_mask is None:
        include_mask = [True] * len(samples)
    errors = [
        pred - ref
        for pred, ref, include in zip(replayed, reference_vals, include_mask)
        if include and math.isfinite(pred) and math.isfinite(ref)
    ]
    return score_error_series_torr(errors)
