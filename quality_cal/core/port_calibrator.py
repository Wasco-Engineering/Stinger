"""Fit and apply per-port calibration models from quality-cal sweep data."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from app.hardware.port import Port
from app.services.pressure_calibration import (
    REFERENCE_MENSOR,
    SENSOR_ALICAT,
    SENSOR_TRANSDUCER,
    apply_error_model,
)
from quality_cal.config import QualitySettings, get_default_config_path
from quality_cal.core.calibration_export import merge_hardware_into_stinger_config
from quality_cal.session import CalibrationPointResult, PortFitSummary

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SensorFitResult:
    sensor: str
    p99_abs_torr: float
    mean_abs_torr: float
    max_abs_torr: float
    passed: bool
    model: Dict[str, Any]


@dataclass(slots=True)
class PortCalibrationFitResult:
    port_id: str
    sweep_csv_path: Path
    transducer: Optional[SensorFitResult]
    alicat: Optional[SensorFitResult]
    error_message: Optional[str] = None

    @property
    def overall_passed(self) -> bool:
        if self.error_message:
            return False
        checks = []
        if self.transducer is not None:
            checks.append(self.transducer.passed)
        if self.alicat is not None:
            checks.append(self.alicat.passed)
        return bool(checks) and all(checks)


def get_stinger_config_path() -> Path:
    from app.core.paths import get_stinger_config_path as _stinger_path

    return _stinger_path()


def fit_port_from_sweep_csv(
    csv_path: Path | str,
    port_id: str,
    settings: QualitySettings,
) -> PortCalibrationFitResult:
    """Fit transducer and Alicat error models vs Mensor from dense sweep CSV."""
    from scripts.optimize_pressure_calibration import (  # noqa: WPS433
        _load_samples,
        _optimize_for_port_sensor,
    )

    path = Path(csv_path)
    if not path.exists():
        return PortCalibrationFitResult(
            port_id=port_id,
            sweep_csv_path=path,
            transducer=None,
            alicat=None,
            error_message=f'Sweep CSV not found: {path}',
        )

    try:
        from app.services.pressure_calibration import filter_samples_pressure_band

        samples = _load_samples([path], port_id)
        samples = filter_samples_pressure_band(
            samples,
            min_psi=0.0,
            max_psi=settings.fit_max_psia,
            reference=REFERENCE_MENSOR,
        )
        if not any(s.mensor_abs_psia is not None for s in samples):
            return PortCalibrationFitResult(
                port_id=port_id,
                sweep_csv_path=path,
                transducer=None,
                alicat=None,
                error_message='Sweep CSV has no Mensor data; cannot fit vs Mensor reference.',
            )

        min_samples = max(30, len(settings.pressure_points_psia) * 2)
        transducer_fit: Optional[SensorFitResult] = None
        alicat_fit: Optional[SensorFitResult] = None

        for sensor in (SENSOR_TRANSDUCER, SENSOR_ALICAT):
            try:
                opt = _optimize_for_port_sensor(
                    port_id=port_id,
                    sensor=sensor,
                    reference=REFERENCE_MENSOR,
                    samples=samples,
                    tolerance_psi=max(0.2, settings.settle_tolerance_psia * 2),
                    static_only=True,
                    holdout_stride=5,
                    alpha_grid=[0.0],
                    pass_threshold_torr=settings.pass_threshold_torr,
                    min_near_target=min_samples,
                )
                best = opt['best']
                fit = SensorFitResult(
                    sensor=sensor,
                    p99_abs_torr=float(best.p99_abs_torr),
                    mean_abs_torr=float(best.mean_abs_torr),
                    max_abs_torr=float(best.max_abs_torr),
                    passed=bool(best.passed),
                    model=dict(best.model),
                )
                if sensor == SENSOR_TRANSDUCER:
                    transducer_fit = fit
                else:
                    alicat_fit = fit
            except Exception as exc:
                logger.warning('Fit failed for %s/%s: %s', port_id, sensor, exc)

        return PortCalibrationFitResult(
            port_id=port_id,
            sweep_csv_path=path,
            transducer=transducer_fit,
            alicat=alicat_fit,
        )
    except Exception as exc:
        logger.exception('Port calibration fit failed')
        return PortCalibrationFitResult(
            port_id=port_id,
            sweep_csv_path=path,
            transducer=None,
            alicat=None,
            error_message=str(exc),
        )


def build_port_config_snippet(
    port_id: str,
    fit: PortCalibrationFitResult,
) -> Dict[str, Any]:
    labjack_port: Dict[str, Any] = {}
    alicat_port: Dict[str, Any] = {}
    if fit.transducer is not None:
        labjack_port['transducer_error_model'] = fit.transducer.model
    if fit.alicat is not None:
        alicat_port['alicat_error_model'] = fit.alicat.model
    hardware: Dict[str, Any] = {
        'labjack': {
            'pressure_filter_alpha': 0.0,
            port_id: labjack_port,
        },
    }
    if alicat_port:
        hardware['alicat'] = {port_id: alicat_port}
    return {'hardware': hardware}


def apply_port_models_to_stinger_config(
    port_id: str,
    fit: PortCalibrationFitResult,
    stinger_path: Optional[Path] = None,
) -> Path:
    """Merge fitted models for one port into stinger_config.yaml."""
    path = stinger_path or get_stinger_config_path()
    snippet = build_port_config_snippet(port_id, fit)
    if path.exists():
        stinger = yaml.safe_load(path.read_text(encoding='utf-8'))
        if not isinstance(stinger, dict):
            raise ValueError(f'Invalid stinger config: {path}')
        merged = merge_hardware_into_stinger_config(stinger, snippet)
    else:
        merged = snippet
    path.write_text(yaml.safe_dump(merged, sort_keys=False), encoding='utf-8')
    logger.info('Wrote calibration models for %s to %s', port_id, path)
    return path


def reload_port_calibration(port: Port, snippet: Dict[str, Any]) -> None:
    """Apply error models on connected hardware without reconnecting."""
    hw = snippet.get('hardware', {})
    lj = hw.get('labjack', {})
    ali = hw.get('alicat', {})
    port_key = port.port_id.value

    port_cfg = lj.get(port_key, {})
    if hasattr(port.daq, 'apply_error_model_config'):
        port.daq.apply_error_model_config(port_cfg.get('transducer_error_model'))
    else:
        port.daq._error_model = port_cfg.get('transducer_error_model')

    ali_cfg = ali.get(port_key, {})
    if hasattr(port.alicat, 'apply_error_model_config'):
        port.alicat.apply_error_model_config(ali_cfg.get('alicat_error_model'))
    else:
        port.alicat._error_model = ali_cfg.get('alicat_error_model')


def fit_summary_from_result(
    port_id: str,
    fit: PortCalibrationFitResult,
    *,
    applied: bool,
) -> PortFitSummary:
    return PortFitSummary(
        port_id=port_id,
        sweep_csv_path=fit.sweep_csv_path,
        transducer_p99_abs_torr=(
            fit.transducer.p99_abs_torr if fit.transducer is not None else None
        ),
        alicat_p99_abs_torr=fit.alicat.p99_abs_torr if fit.alicat is not None else None,
        transducer_passed=bool(fit.transducer.passed) if fit.transducer else False,
        alicat_passed=bool(fit.alicat.passed) if fit.alicat else False,
        applied_to_stinger_config=applied,
        transducer_error_model=(
            fit.transducer.model if fit.transducer is not None else None
        ),
        alicat_error_model=fit.alicat.model if fit.alicat is not None else None,
    )


def rescore_points_with_models(
    points: list[CalibrationPointResult],
    fit: PortCalibrationFitResult,
) -> list[CalibrationPointResult]:
    """Recompute corrected Alicat deviation vs Mensor for display."""
    if fit.alicat is None or fit.alicat.model is None:
        return points
    updated: list[CalibrationPointResult] = []
    for point in points:
        corrected_dev = None
        if (
            point.mensor_used
            and point.mensor_psia is not None
            and point.alicat_psia is not None
        ):
            corrected_alicat = apply_error_model(point.alicat_psia, fit.alicat.model)
            corrected_dev = point.mensor_psia - corrected_alicat
        updated.append(
            CalibrationPointResult(
                port_id=point.port_id,
                point_index=point.point_index,
                point_total=point.point_total,
                target_psia=point.target_psia,
                route=point.route,
                mensor_psia=point.mensor_psia,
                alicat_psia=point.alicat_psia,
                transducer_psia=point.transducer_psia,
                deviation_psia=point.deviation_psia,
                passed=point.passed,
                settle_duration_s=point.settle_duration_s,
                hold_duration_s=point.hold_duration_s,
                sample_count=point.sample_count,
                corrected_deviation_psia=corrected_dev,
                mensor_used=point.mensor_used,
                measured_at=point.measured_at,
            )
        )
    return updated


def format_config_apply_preview(port_id: str, fit: PortCalibrationFitResult) -> str:
    """YAML snippet that would be merged into stinger_config.yaml on Apply."""
    snippet = build_port_config_snippet(port_id, fit)
    text = yaml.safe_dump(snippet, sort_keys=False, default_flow_style=False)
    return f'Config changes for {port_id}:\n\n{text}'


def format_fit_dialog_text(fit: PortCalibrationFitResult, settings: QualitySettings) -> str:
    lines = [
        f'Profile: {settings.profile_label}',
        f'Sweep: {fit.sweep_csv_path.name}',
        '',
    ]
    if fit.error_message:
        lines.append(f'Fit error: {fit.error_message}')
        return '\n'.join(lines)
    if fit.transducer is not None:
        lines.append(
            f'Transducer p99: {fit.transducer.p99_abs_torr:.3f} Torr '
            f'({"PASS" if fit.transducer.passed else "FAIL"} vs {settings.pass_threshold_torr:.1f} Torr)',
        )
    if fit.alicat is not None:
        lines.append(
            f'Alicat p99: {fit.alicat.p99_abs_torr:.3f} Torr '
            f'({"PASS" if fit.alicat.passed else "FAIL"} vs {settings.pass_threshold_torr:.1f} Torr)',
        )
    lines.append('')
    lines.append(format_config_apply_preview(fit.port_id, fit))
    lines.append('')
    lines.append('Apply models to stinger_config.yaml on this machine?')
    return '\n'.join(lines)
