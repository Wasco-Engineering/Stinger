"""Mock preview harness for the quality calibration UI."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from quality_cal.config import load_config, parse_quality_settings
from quality_cal.session import CalibrationPointResult, LeakCheckResult, QualityCalibrationSession
from quality_cal.ui.models import HardwareSnapshot, HardwareStatusEntry
from quality_cal.ui.window import QualityCalibrationWindow


PREVIEW_STATES = (
    'setup-ready',
    'setup-failure',
    'leak-running',
    'calibration-running',
    'calibration-fail',
    'report-pass',
    'report-fail',
)


def _healthy_snapshot() -> HardwareSnapshot:
    entries = (
        HardwareStatusEntry('left_labjack', 'Left LabJack', True, 'Connected | Transducer=14.221 psia'),
        HardwareStatusEntry('left_alicat', 'Left Alicat', True, 'Connected | Pressure=14.687 psia Setpoint=15.000'),
        HardwareStatusEntry('right_labjack', 'Right LabJack', True, 'Connected | Transducer=14.135 psia'),
        HardwareStatusEntry('right_alicat', 'Right Alicat', True, 'Connected | Pressure=14.705 psia Setpoint=15.000'),
        HardwareStatusEntry('mensor', 'Mensor', True, 'Connected | Pressure=14.694 psia'),
    )
    return HardwareSnapshot(
        overall_ok=True,
        summary='5/5 hardware checks passing.',
        discovery_note='Preview mode using mock hardware.',
        entries=entries,
    )


def _partial_snapshot() -> HardwareSnapshot:
    entries = (
        HardwareStatusEntry('left_labjack', 'Left LabJack', True, 'Connected | Transducer=14.221 psia'),
        HardwareStatusEntry('left_alicat', 'Left Alicat', True, 'Connected | Pressure=14.687 psia Setpoint=15.000'),
        HardwareStatusEntry('right_labjack', 'Right LabJack', False, 'Connected | Transducer unavailable; verify wiring.'),
        HardwareStatusEntry('right_alicat', 'Right Alicat', True, 'Connected | Pressure=14.702 psia Setpoint=15.000'),
        HardwareStatusEntry('mensor', 'Mensor', False, 'Connected | Read failed: timeout waiting for response.'),
    )
    return HardwareSnapshot(
        overall_ok=False,
        summary='3/5 hardware checks passing.',
        discovery_note='Preview mode showing partial hardware failure.',
        entries=entries,
    )


def _build_session(*, include_leak_check: bool, passed: bool) -> QualityCalibrationSession:
    session = QualityCalibrationSession(
        technician_name='Nathan B.',
        asset_id='222',
        include_leak_check=include_leak_check,
        started_at=datetime.now() - timedelta(minutes=18),
        completed_at=datetime.now(),
    )
    left_results = []
    for index, target in enumerate([1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0], 1):
        left_results.append(
            CalibrationPointResult(
                port_id='port_a',
                point_index=index,
                point_total=7,
                target_psia=target,
                route='ramp',
                mensor_psia=target + (0.001 if passed or index < 7 else 0.072),
                alicat_psia=target + 0.009,
                transducer_psia=target - (0.003 if passed or index < 7 else 0.061),
                deviation_psia=0.004 if passed or index < 7 else 0.063,
                passed=passed or index < 7,
                settle_duration_s=4.8,
                hold_duration_s=8.0,
                sample_count=32,
            )
        )
    right_results = [
        CalibrationPointResult(
            port_id='port_b',
            point_index=index,
            point_total=7,
            target_psia=target,
            route='ramp',
            mensor_psia=target + 0.001,
            alicat_psia=target + 0.009,
            transducer_psia=target - 0.003,
            deviation_psia=0.004,
            passed=True,
            settle_duration_s=4.8,
            hold_duration_s=8.0,
            sample_count=32,
        )
        for index, target in enumerate([1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0], 1)
    ]
    session.left_port.points = left_results
    session.right_port.points = right_results
    if include_leak_check:
        session.left_port.leak_check = LeakCheckResult(
            port_id='port_a',
            target_psia=100.0,
            duration_s=90.0,
            initial_alicat_psia=100.2,
            final_alicat_psia=100.0,
            initial_transducer_psia=100.1,
            final_transducer_psia=99.9,
            alicat_leak_rate_psi_per_min=0.1333,
            transducer_leak_rate_psi_per_min=0.1400,
            passed=True,
        )
        session.right_port.leak_check = LeakCheckResult(
            port_id='port_b',
            target_psia=100.0,
            duration_s=90.0,
            initial_alicat_psia=100.2,
            final_alicat_psia=99.7,
            initial_transducer_psia=100.1,
            final_transducer_psia=99.6,
            alicat_leak_rate_psi_per_min=0.3333,
            transducer_leak_rate_psi_per_min=0.3555,
            passed=False if not passed else True,
        )
    return session


def create_preview_window(state: str) -> QualityCalibrationWindow:
    config = load_config()
    settings = parse_quality_settings(config)
    window = QualityCalibrationWindow(config=config, settings=settings, preview_mode=True)

    snapshot = _healthy_snapshot()
    session = QualityCalibrationSession(technician_name='Nathan B.', asset_id='222', include_leak_check=True)
    completed = {'setup'}
    current_stage = 'setup'

    if state == 'setup-failure':
        snapshot = _partial_snapshot()
        session = QualityCalibrationSession(technician_name='Nathan B.', asset_id='222', include_leak_check=False)
    elif state == 'leak-running':
        session = _build_session(include_leak_check=True, passed=True)
        current_stage = 'left_leak'
    elif state == 'calibration-running':
        session = _build_session(include_leak_check=True, passed=True)
        completed.update({'left_leak'})
        current_stage = 'left_calibration'
    elif state == 'calibration-fail':
        session = _build_session(include_leak_check=True, passed=False)
        completed.update({'left_leak', 'left_calibration'})
        current_stage = 'left_calibration'
    elif state == 'report-pass':
        session = _build_session(include_leak_check=True, passed=True)
        completed = {
            stage.key
            for stage in window._build_workflow_stages(include_leak_check=True)
            if stage.key != 'report'
        }
        current_stage = 'report'
    elif state == 'report-fail':
        session = _build_session(include_leak_check=True, passed=False)
        completed = {
            stage.key
            for stage in window._build_workflow_stages(include_leak_check=True)
            if stage.key != 'report'
        }
        current_stage = 'report'

    window.session = session
    window._hardware_snapshot = snapshot
    window._completed_stage_keys = set(completed)
    window._current_stage_index = 0
    window._set_stages(window._build_workflow_stages(include_leak_check=session.include_leak_check))

    setup_panel = window._stage_widgets.get('setup')
    if setup_panel is not None:
        setup_panel.set_session_values(session.technician_name, session.asset_id, session.include_leak_check)
        setup_panel.set_hardware_snapshot(snapshot)

    if state == 'leak-running':
        panel = window._run_panel_for('left_leak')
        panel.set_running(True)
        panel.set_progress(42, 'Measuring leak rate... 52s remaining')
        panel.set_live_readings(elapsed_s=38.4, alicat_psia=99.984, transducer_psia=99.971)
        panel.set_ready_message('Mock leak check in progress.')
    elif state == 'calibration-running':
        panel = window._run_panel_for('left_calibration')
        panel.set_running(True)
        panel.set_progress(63, 'Holding point 5/7 at 20.0 psia')
        panel.set_live_readings(mensor_psia=19.998, alicat_psia=20.011, transducer_psia=19.943)
        panel.set_ready_message('Mock calibration in progress.')
    elif state == 'calibration-fail':
        panel = window._run_panel_for('left_calibration')
        for result in session.left_port.points:
            panel.append_point_result(result)
        panel.show_calibration_result(session.left_port.points)

    window.select_stage(current_stage)
    return window


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Preview the quality calibration UI with mock data.')
    parser.add_argument('--state', choices=PREVIEW_STATES, default='setup-ready')
    parser.add_argument('--screenshot', type=Path)
    parser.add_argument('--export-dir', type=Path)
    args = parser.parse_args(argv)

    app = QApplication(sys.argv)
    app.setApplicationName('Quality Calibration Preview')

    if args.export_dir is not None:
        args.export_dir.mkdir(parents=True, exist_ok=True)
        for state in PREVIEW_STATES:
            window = create_preview_window(state)
            window.resize(1600, 960)
            window.show()
            app.processEvents()
            window.grab().save(str(args.export_dir / f'{state}.png'))
            window.close()
        return 0

    window = create_preview_window(args.state)
    window.resize(1600, 960)
    window.show()
    app.processEvents()

    if args.screenshot is not None:
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        window.grab().save(str(args.screenshot))
        return 0

    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
