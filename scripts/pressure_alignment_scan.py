"""Run live Alicat/transducer alignment scans across pressure range."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.hardware.port import Port, PortManager
from app.services.ptp_service import convert_pressure


@dataclass
class Sample:
    timestamp: float
    elapsed_s: float
    port_id: str
    phase: str
    target_abs_psi: Optional[float]
    setpoint_cmd: Optional[float]
    setpoint_ref: Optional[str]
    route: Optional[str]
    transducer_abs_psi: Optional[float]
    transducer_raw_abs_psi: Optional[float]
    alicat_abs_psi: Optional[float]
    mensor_abs_psia: Optional[float]
    alicat_setpoint_raw: Optional[float]
    alicat_pressure_raw: Optional[float]
    alicat_gauge_raw: Optional[float]
    alicat_baro_raw: Optional[float]
    error_psi: Optional[float]


def _log(message: str, progress_log_path: Optional[Path]) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    if progress_log_path is not None:
        progress_log_path.parent.mkdir(parents=True, exist_ok=True)
        with progress_log_path.open('a', encoding='utf-8') as f:
            f.write(line + '\n')


def _load_config(config_path: Path) -> Dict[str, Any]:
    return yaml.safe_load(config_path.read_text(encoding='utf-8'))


def _infer_barometric_psi(reading: Any) -> Optional[float]:
    if reading is None or reading.alicat is None:
        return None
    if reading.alicat.barometric_pressure is not None:
        return reading.alicat.barometric_pressure
    if reading.alicat.pressure is not None and reading.alicat.gauge_pressure is not None:
        return reading.alicat.pressure - reading.alicat.gauge_pressure
    return None


def _normalize_barometric_guess(raw_baro: Optional[float]) -> float:
    """Normalize barometric pressure guess to a plausible PSI value."""
    if raw_baro is None:
        return 14.7
    if 5.0 <= raw_baro <= 20.0:
        return raw_baro
    # Some devices occasionally report Torr-like values before units settle.
    if raw_baro > 20.0:
        torr_converted = convert_pressure(raw_baro, 'Torr', 'PSI')
        if 5.0 <= torr_converted <= 20.0:
            return torr_converted
    return 14.7


def _infer_alicat_abs_psi(reading: Any, fallback_baro: float) -> Optional[float]:
    if reading is None or reading.alicat is None:
        return None
    if reading.alicat.pressure is not None:
        return reading.alicat.pressure
    if reading.alicat.gauge_pressure is not None:
        return reading.alicat.gauge_pressure + fallback_baro
    return None


def _infer_transducer_abs_psi(reading: Any, fallback_baro: float) -> Optional[float]:
    if reading is None or reading.transducer is None:
        return None
    value = reading.transducer.pressure
    reference = str(reading.transducer.pressure_reference or 'absolute').strip().lower()
    if reference == 'gauge':
        return value + fallback_baro
    return value


def _infer_transducer_raw_abs_psi(reading: Any, fallback_baro: float) -> Optional[float]:
    if reading is None or reading.transducer is None:
        return None
    value = reading.transducer.pressure_raw
    if value is None:
        return None
    reference = str(reading.transducer.pressure_reference or 'absolute').strip().lower()
    if reference == 'gauge':
        return value + fallback_baro
    return value


def _infer_setpoint_reference(port: Port, fallback_baro: float) -> str:
    status = port.alicat.read_status()
    if status is None:
        return 'absolute'
    setpoint = status.setpoint
    abs_pressure = status.pressure
    gauge_pressure = status.gauge_pressure

    if gauge_pressure is not None:
        abs_candidate = gauge_pressure + fallback_baro
        if abs(setpoint - abs_candidate) < abs(setpoint - gauge_pressure):
            return 'absolute'
        return 'gauge'

    if abs_pressure is not None:
        gauge_candidate = abs_pressure - fallback_baro
        if abs(setpoint - abs_pressure) <= abs(setpoint - gauge_candidate):
            return 'absolute'
        return 'gauge'

    return 'absolute'


def _command_setpoint_abs(port: Port, target_abs_psi: float, setpoint_ref: str, barometric_psi: float) -> float:
    command_value = target_abs_psi
    if setpoint_ref == 'gauge':
        command_value = target_abs_psi - barometric_psi
    port.alicat.cancel_hold()
    port.set_pressure(command_value)
    return command_value


def _route_for_target(port: Port, target_abs_psi: float, barometric_psi: float) -> str:
    to_vacuum = target_abs_psi < (barometric_psi - 0.3)
    port.set_solenoid(to_vacuum)
    return 'vacuum' if to_vacuum else 'pressure'


def _read_mensor_psia(mensor_reader: Any) -> Optional[float]:
    if mensor_reader is None:
        return None
    try:
        return float(mensor_reader.read_pressure().pressure_psia)
    except Exception:
        return None


def _sample(
    *,
    start_time: float,
    port_id: str,
    phase: str,
    target_abs_psi: Optional[float],
    setpoint_cmd: Optional[float],
    setpoint_ref: Optional[str],
    route: Optional[str],
    reading: Any,
    fallback_baro: float,
    mensor_reader: Any = None,
) -> Sample:
    now = time.time()
    baro = _infer_barometric_psi(reading)
    baro_used = baro if baro is not None else fallback_baro
    trans_abs = _infer_transducer_abs_psi(reading, baro_used)
    trans_raw_abs = _infer_transducer_raw_abs_psi(reading, baro_used)
    alicat_abs = _infer_alicat_abs_psi(reading, baro_used)
    error = None
    if trans_abs is not None and alicat_abs is not None:
        error = trans_abs - alicat_abs

    return Sample(
        timestamp=now,
        elapsed_s=now - start_time,
        port_id=port_id,
        phase=phase,
        target_abs_psi=target_abs_psi,
        setpoint_cmd=setpoint_cmd,
        setpoint_ref=setpoint_ref,
        route=route,
        transducer_abs_psi=trans_abs,
        transducer_raw_abs_psi=trans_raw_abs,
        alicat_abs_psi=alicat_abs,
        mensor_abs_psia=_read_mensor_psia(mensor_reader),
        alicat_setpoint_raw=reading.alicat.setpoint if reading and reading.alicat else None,
        alicat_pressure_raw=reading.alicat.pressure if reading and reading.alicat else None,
        alicat_gauge_raw=reading.alicat.gauge_pressure if reading and reading.alicat else None,
        alicat_baro_raw=reading.alicat.barometric_pressure if reading and reading.alicat else None,
        error_psi=error,
    )


def _wait_for_target(
    *,
    port: Port,
    port_id: str,
    phase: str,
    target_abs_psi: float,
    setpoint_cmd: float,
    setpoint_ref: str,
    route: str,
    start_time: float,
    samples: List[Sample],
    baro_guess: float,
    hold_after_reached_s: float,
    timeout_s: float,
    sample_period_s: float,
    settle_tolerance_psi: float,
    on_sample: Callable[[Sample], None],
    progress_log_path: Optional[Path],
    mensor_reader: Any = None,
) -> float:
    reached_time: Optional[float] = None
    begin = time.time()
    baro_current = baro_guess
    next_status = begin + 5.0
    while True:
        reading = port.read_all()
        inferred = _infer_barometric_psi(reading)
        if inferred is not None:
            baro_current = inferred
        sample = _sample(
            start_time=start_time,
            port_id=port_id,
            phase=phase,
            target_abs_psi=target_abs_psi,
            setpoint_cmd=setpoint_cmd,
            setpoint_ref=setpoint_ref,
            route=route,
            reading=reading,
            fallback_baro=baro_current,
            mensor_reader=mensor_reader,
        )
        samples.append(sample)
        on_sample(sample)

        near_ref = sample.mensor_abs_psia if sample.mensor_abs_psia is not None else sample.alicat_abs_psi
        if near_ref is not None:
            if abs(near_ref - target_abs_psi) <= settle_tolerance_psi:
                if reached_time is None:
                    reached_time = time.time()
                    _log(
                        f"{port_id} {phase}: reached target {target_abs_psi:.2f} psia, stabilizing",
                        progress_log_path,
                    )
                elif time.time() - reached_time >= hold_after_reached_s:
                    _log(
                        f"{port_id} {phase}: completed near target {target_abs_psi:.2f} psia",
                        progress_log_path,
                    )
                    return baro_current
            else:
                reached_time = None

        now = time.time()
        if now >= next_status:
            _log(
                f"{port_id} {phase}: alicat={sample.alicat_abs_psi} transducer={sample.transducer_abs_psi} error={sample.error_psi}",
                progress_log_path,
            )
            next_status = now + 5.0

        if now - begin >= timeout_s:
            _log(
                f"{port_id} {phase}: timeout after {timeout_s:.0f}s (last alicat={sample.alicat_abs_psi})",
                progress_log_path,
            )
            return baro_current
        time.sleep(sample_period_s)


def _collect_static_hold(
    *,
    port: Port,
    port_id: str,
    phase: str,
    target_abs_psi: float,
    setpoint_cmd: float,
    setpoint_ref: str,
    route: str,
    start_time: float,
    samples: List[Sample],
    baro_guess: float,
    hold_duration_s: float,
    sample_period_s: float,
    on_sample: Callable[[Sample], None],
    progress_log_path: Optional[Path],
    mensor_reader: Any = None,
) -> float:
    begin = time.time()
    baro_current = baro_guess
    next_status = begin + 5.0
    while time.time() - begin < hold_duration_s:
        reading = port.read_all()
        inferred = _infer_barometric_psi(reading)
        if inferred is not None:
            baro_current = inferred
        sample = _sample(
                start_time=start_time,
                port_id=port_id,
                phase=phase,
                target_abs_psi=target_abs_psi,
                setpoint_cmd=setpoint_cmd,
                setpoint_ref=setpoint_ref,
                route=route,
                reading=reading,
                fallback_baro=baro_current,
                mensor_reader=mensor_reader,
            )
        samples.append(sample)
        on_sample(sample)
        now = time.time()
        if now >= next_status:
            _log(
                f"{port_id} {phase}: holding alicat={sample.alicat_abs_psi} "
                f"mensor={sample.mensor_abs_psia} transducer={sample.transducer_abs_psi} error={sample.error_psi}",
                progress_log_path,
            )
            next_status = now + 5.0
        time.sleep(sample_period_s)
    _log(f"{port_id} {phase}: static hold complete", progress_log_path)
    return baro_current


def _fit_line(xs: List[float], ys: List[float]) -> Dict[str, Optional[float]]:
    if len(xs) < 2:
        return {'slope': None, 'intercept': None}
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    if ss_xx == 0:
        return {'slope': 0.0, 'intercept': mean_y}
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    return {'slope': slope, 'intercept': intercept}


def _summarize_port(samples: Iterable[Sample], port_id: str) -> Dict[str, Any]:
    port_samples = [s for s in samples if s.port_id == port_id and s.error_psi is not None and s.alicat_abs_psi is not None]
    dynamic_up = [s for s in port_samples if s.phase == 'dynamic_up']
    dynamic_down = [s for s in port_samples if s.phase == 'dynamic_down']
    static = [s for s in port_samples if s.phase.startswith('static_')]

    def metrics(group: List[Sample]) -> Dict[str, Optional[float]]:
        if not group:
            return {'n': 0, 'mean_error': None, 'stdev_error': None, 'p95_abs_error': None}
        errors = [s.error_psi for s in group if s.error_psi is not None]
        abs_errors = sorted(abs(e) for e in errors)
        idx_95 = min(len(abs_errors) - 1, int(round(0.95 * (len(abs_errors) - 1))))
        return {
            'n': len(errors),
            'mean_error': statistics.fmean(errors),
            'stdev_error': statistics.pstdev(errors) if len(errors) > 1 else 0.0,
            'p95_abs_error': abs_errors[idx_95],
        }

    def linear(group: List[Sample]) -> Dict[str, Optional[float]]:
        xs = [s.alicat_abs_psi for s in group if s.alicat_abs_psi is not None and s.error_psi is not None]
        ys = [s.error_psi for s in group if s.alicat_abs_psi is not None and s.error_psi is not None]
        return _fit_line(xs, ys)

    overall = metrics(port_samples)
    static_metrics = metrics(static)
    dynamic_up_metrics = metrics(dynamic_up)
    dynamic_down_metrics = metrics(dynamic_down)
    static_line = linear(static)
    dynamic_line = linear(dynamic_up + dynamic_down)

    recommended_offset = None
    if static:
        recommended_offset = -statistics.fmean([s.error_psi for s in static if s.error_psi is not None])

    return {
        'port_id': port_id,
        'overall': overall,
        'dynamic_up': dynamic_up_metrics,
        'dynamic_down': dynamic_down_metrics,
        'static': static_metrics,
        'dynamic_error_vs_pressure': dynamic_line,
        'static_error_vs_pressure': static_line,
        'recommended_transducer_offset_psi': recommended_offset,
    }


def run_scan(
    *,
    config_path: Path,
    output_csv: Path,
    ramp_rate_psi_s: float,
    min_abs_psi: float,
    max_abs_psi: float,
    static_points: List[float],
    static_hold_s: float,
    sample_hz: float,
    ports: Optional[List[str]] = None,
    progress_log_path: Optional[Path] = None,
    mode: str = 'both',
    settle_tolerance_psi: float = 0.6,
    settle_hold_s: float = 4.0,
    settle_timeout_s: float = 240.0,
    force_setpoint_ref: Optional[str] = None,
    capture_raw_profile: bool = False,
    with_mensor: bool = False,
    mensor_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config = _load_config(config_path)
    manager = PortManager(config)
    manager.initialize_ports()
    if not manager.connect_all():
        raise RuntimeError('Failed to connect all ports')

    mensor_reader = None
    if with_mensor:
        from quality_cal.core.mensor_reader import MensorReader

        mensor_cfg = dict(mensor_config or {})
        if not mensor_cfg:
            qc_path = PROJECT_ROOT / 'quality_cal_config.yaml'
            if qc_path.exists():
                mensor_cfg = dict(yaml.safe_load(qc_path.read_text(encoding='utf-8')).get('hardware', {}).get('mensor', {}) or {})
        mensor_reader = MensorReader(mensor_cfg)
        if not mensor_reader.connect():
            raise RuntimeError(f'Mensor connect failed: {mensor_reader.status}')

    all_samples: List[Sample] = []
    run_start = time.time()
    sample_period_s = 1.0 / sample_hz
    selected_ports = ports or ['port_a', 'port_b']

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    csv_file = output_csv.open('w', newline='', encoding='utf-8')
    writer = csv.DictWriter(csv_file, fieldnames=list(Sample.__annotations__.keys()))
    writer.writeheader()

    def on_sample(sample: Sample) -> None:
        writer.writerow({k: getattr(sample, k) for k in Sample.__annotations__.keys()})
        csv_file.flush()

    try:
        scan_mode = (mode or 'both').strip().lower()
        if scan_mode not in {'both', 'static', 'dynamic'}:
            raise ValueError(f'Unsupported mode: {mode}')

        _log(
            f"Starting scan ports={selected_ports}, range={min_abs_psi}-{max_abs_psi} psia, rate={ramp_rate_psi_s} psi/s",
            progress_log_path,
        )
        for port_id in selected_ports:
            port = manager.get_port(port_id)
            if port is None:
                _log(f"Skipping {port_id}: port not available", progress_log_path)
                continue

            # Force PSI units for stable control/analysis across ports.
            try:
                port.alicat.configure_units_from_ptp('1')
                time.sleep(0.15)
            except Exception:
                _log(f"{port_id}: warning - failed to force PSI units", progress_log_path)

            if capture_raw_profile:
                # Optional capture mode for offline model optimization:
                # disable offset/model/filter in-memory for cleaner raw dataset.
                port.daq.pressure_offset = 0.0
                port.daq._error_model = None
                port.daq._nonlinear_breakpoint_psi = 0.0
                port.daq._nonlinear_low_slope = 0.0
                port.daq._nonlinear_low_intercept = 0.0
                port.daq._nonlinear_high_slope = 0.0
                port.daq._nonlinear_high_intercept = 0.0
                port.daq._filter_alpha = 0.0
                port.daq._ema_pressure = None
                _log(
                    f'{port_id}: capture_raw_profile enabled (offset/model/filter disabled in-memory)',
                    progress_log_path,
                )

            initial = port.read_all()
            baro = _infer_barometric_psi(initial)
            baro_guess = _normalize_barometric_guess(baro)
            setpoint_ref = _infer_setpoint_reference(port, baro_guess)
            if force_setpoint_ref in {'absolute', 'gauge'}:
                setpoint_ref = force_setpoint_ref
            _log(
                f"{port_id}: initial baro={baro_guess:.3f}, inferred setpoint_ref={setpoint_ref}",
                progress_log_path,
            )

            port.alicat.set_ramp_rate(ramp_rate_psi_s)

            if scan_mode in {'both', 'dynamic'}:
                route = _route_for_target(port, min_abs_psi, baro_guess)
                cmd = _command_setpoint_abs(port, min_abs_psi, setpoint_ref, baro_guess)
                _log(
                    f"{port_id}: phase dynamic_to_min target={min_abs_psi:.2f} route={route} cmd={cmd:.3f}",
                    progress_log_path,
                )
                baro_guess = _wait_for_target(
                    port=port,
                    port_id=port_id,
                    phase='dynamic_to_min',
                    target_abs_psi=min_abs_psi,
                    setpoint_cmd=cmd,
                    setpoint_ref=setpoint_ref,
                    route=route,
                    start_time=run_start,
                    samples=all_samples,
                    baro_guess=baro_guess,
                    hold_after_reached_s=settle_hold_s,
                    timeout_s=settle_timeout_s,
                    sample_period_s=sample_period_s,
                    settle_tolerance_psi=settle_tolerance_psi,
                    on_sample=on_sample,
                    progress_log_path=progress_log_path,
                    mensor_reader=mensor_reader,
                )

                route = _route_for_target(port, max_abs_psi, baro_guess)
                cmd = _command_setpoint_abs(port, max_abs_psi, setpoint_ref, baro_guess)
                _log(
                    f"{port_id}: phase dynamic_up target={max_abs_psi:.2f} route={route} cmd={cmd:.3f}",
                    progress_log_path,
                )
                baro_guess = _wait_for_target(
                    port=port,
                    port_id=port_id,
                    phase='dynamic_up',
                    target_abs_psi=max_abs_psi,
                    setpoint_cmd=cmd,
                    setpoint_ref=setpoint_ref,
                    route=route,
                    start_time=run_start,
                    samples=all_samples,
                    baro_guess=baro_guess,
                    hold_after_reached_s=settle_hold_s,
                    timeout_s=settle_timeout_s,
                    sample_period_s=sample_period_s,
                    settle_tolerance_psi=settle_tolerance_psi,
                    on_sample=on_sample,
                    progress_log_path=progress_log_path,
                    mensor_reader=mensor_reader,
                )

                route = _route_for_target(port, min_abs_psi, baro_guess)
                cmd = _command_setpoint_abs(port, min_abs_psi, setpoint_ref, baro_guess)
                _log(
                    f"{port_id}: phase dynamic_down target={min_abs_psi:.2f} route={route} cmd={cmd:.3f}",
                    progress_log_path,
                )
                baro_guess = _wait_for_target(
                    port=port,
                    port_id=port_id,
                    phase='dynamic_down',
                    target_abs_psi=min_abs_psi,
                    setpoint_cmd=cmd,
                    setpoint_ref=setpoint_ref,
                    route=route,
                    start_time=run_start,
                    samples=all_samples,
                    baro_guess=baro_guess,
                    hold_after_reached_s=settle_hold_s,
                    timeout_s=settle_timeout_s,
                    sample_period_s=sample_period_s,
                    settle_tolerance_psi=settle_tolerance_psi,
                    on_sample=on_sample,
                    progress_log_path=progress_log_path,
                    mensor_reader=mensor_reader,
                )

            if scan_mode in {'both', 'static'}:
                for point in static_points:
                    route = _route_for_target(port, point, baro_guess)
                    cmd = _command_setpoint_abs(port, point, setpoint_ref, baro_guess)
                    phase = f'static_{int(round(point))}'
                    _log(
                        f"{port_id}: phase {phase} target={point:.2f} route={route} cmd={cmd:.3f}",
                        progress_log_path,
                    )
                    baro_guess = _wait_for_target(
                        port=port,
                        port_id=port_id,
                        phase=phase,
                        target_abs_psi=point,
                        setpoint_cmd=cmd,
                        setpoint_ref=setpoint_ref,
                        route=route,
                        start_time=run_start,
                        samples=all_samples,
                        baro_guess=baro_guess,
                        hold_after_reached_s=settle_hold_s,
                        timeout_s=settle_timeout_s,
                        sample_period_s=sample_period_s,
                        settle_tolerance_psi=settle_tolerance_psi,
                        on_sample=on_sample,
                        progress_log_path=progress_log_path,
                        mensor_reader=mensor_reader,
                    )
                    baro_guess = _collect_static_hold(
                        port=port,
                        port_id=port_id,
                        phase=phase,
                        target_abs_psi=point,
                        setpoint_cmd=cmd,
                        setpoint_ref=setpoint_ref,
                        route=route,
                        start_time=run_start,
                        samples=all_samples,
                        baro_guess=baro_guess,
                        hold_duration_s=static_hold_s,
                        sample_period_s=sample_period_s,
                        on_sample=on_sample,
                        progress_log_path=progress_log_path,
                        mensor_reader=mensor_reader,
                    )

            port.alicat.exhaust()
            port.set_solenoid(False)
            _log(f"{port_id}: completed all phases", progress_log_path)

    finally:
        csv_file.close()
        if mensor_reader is not None:
            mensor_reader.close()
        manager.disconnect_all()

    summary = {
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'csv_path': str(output_csv),
        'total_samples': len(all_samples),
        'ports': [_summarize_port(all_samples, port_id) for port_id in selected_ports],
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description='Run Alicat/transducer alignment scan')
    parser.add_argument('--config', default='stinger_config.yaml')
    parser.add_argument('--output-csv', default='logs/pressure_alignment_scan.csv')
    parser.add_argument('--ramp-rate', type=float, default=0.5)
    parser.add_argument('--min-abs', type=float, default=0.0)
    parser.add_argument('--max-abs', type=float, default=100.0)
    parser.add_argument('--static-points', default='5,20,40,60,80,95')
    parser.add_argument('--static-hold-s', type=float, default=20.0)
    parser.add_argument('--sample-hz', type=float, default=5.0)
    parser.add_argument('--ports', default='port_a,port_b')
    parser.add_argument('--progress-log', default='logs/pressure_alignment_scan_progress.log')
    parser.add_argument('--mode', default='both', choices=['both', 'static', 'dynamic'])
    parser.add_argument('--settle-hold-s', type=float, default=4.0)
    parser.add_argument('--settle-timeout-s', type=float, default=240.0)
    parser.add_argument('--settle-tolerance-psi', type=float, default=0.6)
    parser.add_argument('--force-setpoint-ref', default='auto', choices=['auto', 'absolute', 'gauge'])
    parser.add_argument(
        '--capture-raw-profile',
        action='store_true',
        help='Disable in-memory offset/model/filter to capture cleaner calibration input data.',
    )
    parser.add_argument(
        '--with-mensor',
        action='store_true',
        help='Log Mensor reference column (uses quality_cal_config.yaml hardware.mensor).',
    )
    args = parser.parse_args()

    static_points = [float(x.strip()) for x in args.static_points.split(',') if x.strip()]
    ports = [x.strip() for x in args.ports.split(',') if x.strip()]
    summary = run_scan(
        config_path=Path(args.config),
        output_csv=Path(args.output_csv),
        ramp_rate_psi_s=args.ramp_rate,
        min_abs_psi=args.min_abs,
        max_abs_psi=args.max_abs,
        static_points=static_points,
        static_hold_s=args.static_hold_s,
        sample_hz=args.sample_hz,
        ports=ports,
        progress_log_path=Path(args.progress_log),
        mode=args.mode,
        settle_hold_s=args.settle_hold_s,
        settle_timeout_s=args.settle_timeout_s,
        settle_tolerance_psi=args.settle_tolerance_psi,
        force_setpoint_ref=(None if args.force_setpoint_ref == 'auto' else args.force_setpoint_ref),
        capture_raw_profile=args.capture_raw_profile,
        with_mensor=args.with_mensor,
    )
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
