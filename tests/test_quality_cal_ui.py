from __future__ import annotations

import os

import pytest
from PyQt6.QtWidgets import QApplication

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from quality_cal.config import load_config, parse_quality_settings
from quality_cal.preview import create_preview_window
from quality_cal.ui.models import HardwareSnapshot, HardwareStatusEntry
from quality_cal.ui.views import SetupPanel
from quality_cal.ui.window import QualityCalibrationWindow


def _settings_and_config() -> tuple[dict, object]:
    config = load_config()
    settings = parse_quality_settings(config)
    return config, settings


def _healthy_snapshot() -> HardwareSnapshot:
    return HardwareSnapshot(
        overall_ok=True,
        summary='5/5 hardware checks passing.',
        discovery_note='Test snapshot.',
        entries=(
            HardwareStatusEntry('left_labjack', 'Left LabJack', True, 'Connected'),
            HardwareStatusEntry('left_alicat', 'Left Alicat', True, 'Connected'),
            HardwareStatusEntry('right_labjack', 'Right LabJack', True, 'Connected'),
            HardwareStatusEntry('right_alicat', 'Right Alicat', True, 'Connected'),
            HardwareStatusEntry('mensor', 'Mensor', True, 'Connected'),
        ),
    )


@pytest.fixture(scope='module')
def app():
    instance = QApplication.instance()
    if instance is not None:
        return instance
    return QApplication([])


def _build_window(app) -> QualityCalibrationWindow:
    config, settings = _settings_and_config()
    window = QualityCalibrationWindow(config=config, settings=settings, preview_mode=True)
    return window


def test_workflow_stage_generation_without_leak_check(app):
    window = _build_window(app)
    setup_panel = window._stage_widgets['setup']
    assert isinstance(setup_panel, SetupPanel)
    setup_panel.set_hardware_snapshot(_healthy_snapshot())
    window._handle_setup_submit(
        {
            'technician_name': 'Nathan',
            'asset_id': '222',
            'include_leak_check': False,
            'profile_id': 'mensor_0_30',
        }
    )

    assert window.workflow_stage_keys() == [
        'setup',
        'confirm_left',
        'left_calibration',
        'confirm_right',
        'right_calibration',
        'report',
    ]
    window.close()


def test_workflow_stage_generation_with_leak_check(app):
    window = _build_window(app)
    setup_panel = window._stage_widgets['setup']
    assert isinstance(setup_panel, SetupPanel)
    setup_panel.set_hardware_snapshot(_healthy_snapshot())
    window._handle_setup_submit(
        {
            'technician_name': 'Nathan',
            'asset_id': '222',
            'include_leak_check': True,
            'profile_id': 'mensor_0_30',
        }
    )

    assert window.workflow_stage_keys() == [
        'setup',
        'left_leak',
        'confirm_left',
        'left_calibration',
        'confirm_right',
        'right_leak',
        'right_calibration',
        'report',
    ]
    window.close()


def test_manual_gating_and_mensor_checkpoint(app):
    window = _build_window(app)
    setup_panel = window._stage_widgets['setup']
    assert isinstance(setup_panel, SetupPanel)
    setup_panel.set_hardware_snapshot(_healthy_snapshot())
    window._handle_setup_submit(
        {
            'technician_name': 'Nathan',
            'asset_id': '222',
            'include_leak_check': False,
            'profile_id': 'mensor_0_30',
        }
    )

    window.select_stage('left_calibration')
    assert window.current_stage_key() == 'left_calibration'
    assert not window.next_button.isEnabled()

    window._mark_stage_complete('left_calibration')
    assert window.next_button.isEnabled()

    window.select_stage('confirm_right')
    assert not window.next_button.isEnabled()
    move_panel = window._stage_widgets['confirm_right']
    move_panel.confirm_button.click()
    assert window.next_button.isEnabled()
    window.close()


def test_preview_report_rendering(app):
    window = create_preview_window('report-pass')
    window.resize(1920, 1080)
    window.show()
    app.processEvents()

    report_panel = window._stage_widgets['report']
    assert report_panel.banner_title.text() == 'PASS'
    assert 'Quality Calibration Certificate' in report_panel.browser.toPlainText()
    window.close()


def test_preview_layout_has_no_primary_horizontal_scroll(app):
    for state in ('setup-ready', 'report-fail'):
        window = create_preview_window(state)
        window.resize(1920, 1080)
        window.show()
        app.processEvents()
        current_scroll = window.stack.currentWidget()
        assert current_scroll.horizontalScrollBar().maximum() == 0
        window.close()
