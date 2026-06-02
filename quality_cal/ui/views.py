"""Standalone view widgets for the quality calibration shell."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtPrintSupport import QPrintDialog, QPrinter
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from quality_cal.config import (
    PROFILE_CAL10_WCS02075,
    PROFILE_HIGH_0_115,
    PROFILE_MENSOR_0_30,
    build_pressure_points_for_profile,
    estimate_profile_duration_s,
    parse_quality_settings,
)

from quality_cal.config import QualitySettings
from quality_cal.core.report_generator import (
    build_report_html,
    build_text_document,
    default_csv_filename,
    default_report_filename,
    export_report_csv,
    export_report_pdf,
)
from quality_cal.session import CalibrationPointResult, LeakCheckResult, QualityCalibrationSession
from quality_cal.ui.models import HardwareSnapshot, WorkflowStage
from quality_cal.ui.styles import COLORS, STYLES, TYPOGRAPHY


def _frame() -> QFrame:
    frame = QFrame()
    frame.setProperty('panelRole', 'card')
    frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    return frame


def _headline(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setProperty('textRole', 'sectionTitle')
    return label


def _body(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setProperty('textRole', 'body')
    return label


class WorkflowRail(QWidget):
    """Vertical stage rail shown at the left of the shell."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)

    def set_stages(
        self,
        stages: list[WorkflowStage],
        current_index: int,
        completed: set[str],
    ) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for index, stage in enumerate(stages):
            state = 'current' if index == current_index else 'complete' if stage.key in completed else 'pending'
            frame = _frame()
            frame.setProperty('stageState', state)
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(4)

            badge = QLabel(str(index + 1))
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(28, 28)
            badge.setProperty('badgeState', state)

            top_row = QHBoxLayout()
            top_row.setContentsMargins(0, 0, 0, 0)
            top_row.addWidget(badge, 0)

            title = QLabel(stage.title)
            title.setWordWrap(True)
            title.setProperty('textRole', 'stageTitle')
            top_row.addWidget(title, 1)
            layout.addLayout(top_row)

            desc = QLabel(stage.description)
            desc.setWordWrap(True)
            desc.setProperty('textRole', 'stageDescription')
            desc.setVisible(state == 'current')
            layout.addWidget(desc)

            self._layout.addWidget(frame)

        self._layout.addStretch(1)


class SetupPanel(QWidget):
    """Session details and hardware readiness screen."""

    refresh_requested = pyqtSignal()
    submit_requested = pyqtSignal(dict)

    def __init__(self, parent=None, *, config: dict | None = None) -> None:
        super().__init__(parent)
        self._snapshot: HardwareSnapshot | None = None
        self._config = config or {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        intro = _frame()
        intro_layout = QVBoxLayout(intro)
        intro_layout.setContentsMargins(24, 24, 24, 24)
        intro_layout.setSpacing(8)
        intro_layout.addWidget(_headline('Session setup and hardware readiness'))
        intro_layout.addWidget(
            _body(
                'Enter operator details, verify all hardware, then begin the run when the station is ready.'
            )
        )
        layout.addWidget(intro)

        profile_card = _frame()
        profile_layout = QVBoxLayout(profile_card)
        profile_layout.setContentsMargins(24, 24, 24, 24)
        profile_layout.setSpacing(14)
        profile_layout.addWidget(_headline('Calibration profile'))
        profile_layout.addWidget(
            _body(
                'Choose the pressure sweep for this session. Models are fitted vs Mensor '
                'and can be applied to stinger_config.yaml on this machine after each port.'
            )
        )
        self._profile_group = QButtonGroup(self)
        profile_row = QHBoxLayout()
        profile_row.setSpacing(16)
        self._profile_radios: dict[str, QRadioButton] = {}
        for profile_id, title, blurb in (
            (
                PROFILE_CAL10_WCS02075,
                'CAL 10 WCS02075',
                'WI Rev 000 setpoints (115→0.05 PSIA), automated on each port.',
            ),
            (
                PROFILE_MENSOR_0_30,
                'Mensor 0–30 PSIA',
                'Dense 1 PSI steps, Mensor on all points. Best for 1 Torr calibration.',
            ),
            (
                PROFILE_HIGH_0_115,
                'Dense 0–115 PSIA',
                '0–30 @ 1 PSI, then 35–115 @ 5 PSI. More points than CAL 10.',
            ),
        ):
            frame = QFrame()
            frame.setProperty('panelRole', 'soft')
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(16, 16, 16, 16)
            frame_layout.setSpacing(8)
            radio = QRadioButton(title)
            radio.setProperty('profileId', profile_id)
            self._profile_group.addButton(radio)
            self._profile_radios[profile_id] = radio
            frame_layout.addWidget(radio)
            frame_layout.addWidget(_body(blurb))
            profile_row.addWidget(frame, 1)
        self._profile_radios[PROFILE_CAL10_WCS02075].setChecked(True)
        profile_layout.addLayout(profile_row)
        self.profile_detail_label = QLabel('')
        self.profile_detail_label.setProperty('textRole', 'muted')
        self.profile_detail_label.setWordWrap(True)
        profile_layout.addWidget(self.profile_detail_label)
        self._profile_group.buttonClicked.connect(lambda _: self._update_profile_detail())
        layout.addWidget(profile_card)
        self._update_profile_detail()

        grid = QHBoxLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(20)
        layout.addLayout(grid, 1)

        form_card = _frame()
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(24, 24, 24, 24)
        form_layout.setSpacing(16)
        form_layout.addWidget(_headline('Operator details'))

        self.technician_input = QLineEdit()
        self.technician_input.setPlaceholderText('NB')
        self.technician_input.setMaxLength(12)
        self.technician_input.textChanged.connect(self._sync_button_state)
        self.asset_input = QLineEdit('222')
        self.asset_input.setPlaceholderText('Asset ID')
        self.asset_input.textChanged.connect(self._sync_button_state)
        self.leak_check_checkbox = QCheckBox('Include port leak check')
        self.leak_check_checkbox.setChecked(False)

        form_grid = QGridLayout()
        form_grid.setHorizontalSpacing(14)
        form_grid.setVerticalSpacing(14)
        form_grid.addWidget(_body('Technician ID'), 0, 0)
        form_grid.addWidget(self.technician_input, 0, 1)
        form_grid.addWidget(_body('Asset ID'), 1, 0)
        form_grid.addWidget(self.asset_input, 1, 1)
        form_grid.addWidget(self.leak_check_checkbox, 2, 0, 1, 2)
        form_layout.addLayout(form_grid)

        self.validation_label = QLabel('')
        self.validation_label.setWordWrap(True)
        self.validation_label.setProperty('tone', 'warning')
        self.validation_label.hide()
        form_layout.addWidget(self.validation_label)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.begin_button = QPushButton('Begin Session')
        self.begin_button.setObjectName('primaryButton')
        self.begin_button.clicked.connect(self._emit_submit)
        action_row.addWidget(self.begin_button)
        form_layout.addLayout(action_row)
        form_layout.addStretch(1)
        grid.addWidget(form_card, 1)

        hardware_card = _frame()
        hardware_layout = QVBoxLayout(hardware_card)
        hardware_layout.setContentsMargins(24, 24, 24, 24)
        hardware_layout.setSpacing(16)
        hardware_layout.addWidget(_headline('Hardware verification'))

        self.status_title = QLabel('Verification has not run yet.')
        self.status_title.setProperty('textRole', 'statusTitle')
        self.status_title.setWordWrap(True)
        hardware_layout.addWidget(self.status_title)

        self.summary_label = QLabel('Run Refresh Hardware to detect Alicats, LabJack channels, and the Mensor.')
        self.summary_label.setProperty('textRole', 'body')
        self.summary_label.setWordWrap(True)
        hardware_layout.addWidget(self.summary_label)

        self.discovery_label = QLabel('')
        self.discovery_label.setProperty('textRole', 'muted')
        self.discovery_label.setWordWrap(True)
        hardware_layout.addWidget(self.discovery_label)

        self.entry_container = QWidget()
        self.entry_layout = QVBoxLayout(self.entry_container)
        self.entry_layout.setContentsMargins(0, 0, 0, 0)
        self.entry_layout.setSpacing(12)
        hardware_layout.addWidget(self.entry_container)

        hardware_actions = QHBoxLayout()
        hardware_actions.addStretch(1)
        self.refresh_button = QPushButton('Refresh Hardware')
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        hardware_actions.addWidget(self.refresh_button)
        hardware_layout.addLayout(hardware_actions)
        grid.addWidget(hardware_card, 1)

        self._sync_button_state()

    def _selected_profile_id(self) -> str:
        for profile_id, radio in self._profile_radios.items():
            if radio.isChecked():
                return profile_id
        return PROFILE_MENSOR_0_30

    def _update_profile_detail(self) -> None:
        profile_id = self._selected_profile_id()
        try:
            settings = parse_quality_settings(self._config, profile_id=profile_id)
            n = len(settings.pressure_points_psia)
            est_min = estimate_profile_duration_s(settings) / 60.0
            mensor_note = (
                'Mensor required on all points.'
                if settings.require_mensor
                else f'Mensor ≤{settings.mensor_max_psia:.0f} PSIA; disconnect prompt above 30.'
            )
            self.profile_detail_label.setText(
                f'{settings.profile_label}: {n} points, ~{est_min:.0f} min per port. {mensor_note}',
            )
        except Exception as exc:
            points = build_pressure_points_for_profile(profile_id, self._config.get('quality', {}))
            self.profile_detail_label.setText(f'{len(points)} points. ({exc})')

    def _emit_submit(self) -> None:
        if not self._validate():
            return
        self.submit_requested.emit(
            {
                'technician_name': self.technician_input.text().strip(),
                'asset_id': self.asset_input.text().strip(),
                'include_leak_check': self.leak_check_checkbox.isChecked(),
                'profile_id': self._selected_profile_id(),
            }
        )

    def _validate(self) -> bool:
        message = ''
        if not self.technician_input.text().strip():
            message = 'Enter a technician ID before starting.'
        elif not self.asset_input.text().strip():
            message = 'Enter an asset ID before starting.'
        elif self._snapshot is None:
            message = 'Run hardware refresh before starting the session.'
        elif not self._snapshot.overall_ok:
            message = 'All hardware checks must pass before the session can begin.'

        self.validation_label.setVisible(bool(message))
        self.validation_label.setText(message)
        return not message

    def _sync_button_state(self) -> None:
        can_begin = bool(self.technician_input.text().strip()) and bool(self.asset_input.text().strip())
        if self._snapshot is not None:
            can_begin = can_begin and self._snapshot.overall_ok
        else:
            can_begin = False
        self.begin_button.setEnabled(can_begin)
        if self.validation_label.isVisible():
            self._validate()

    def set_busy(self, busy: bool) -> None:
        self.refresh_button.setEnabled(not busy)
        if busy:
            self.status_title.setText('Refreshing hardware status...')
        self._sync_button_state()

    def set_session_values(self, technician_name: str, asset_id: str, include_leak_check: bool) -> None:
        self.technician_input.setText(technician_name)
        self.asset_input.setText(asset_id)
        self.leak_check_checkbox.setChecked(include_leak_check)
        self._sync_button_state()

    def set_hardware_snapshot(self, snapshot: HardwareSnapshot) -> None:
        self._snapshot = snapshot
        self.status_title.setText('Hardware ready.' if snapshot.overall_ok else 'Hardware needs attention.')
        self.summary_label.setText(snapshot.summary)
        self.discovery_label.setText(snapshot.discovery_note)

        while self.entry_layout.count():
            item = self.entry_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for entry in snapshot.entries:
            card = _frame()
            card.setProperty('hardwareState', 'ok' if entry.ok else 'error')
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 16, 16, 16)
            card_layout.setSpacing(8)

            top_row = QHBoxLayout()
            top_row.setContentsMargins(0, 0, 0, 0)
            title = QLabel(entry.label)
            title.setProperty('textRole', 'subsectionTitle')
            top_row.addWidget(title, 1)

            badge = QLabel('Ready' if entry.ok else 'Check')
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedWidth(88)
            badge.setProperty('chipState', 'success' if entry.ok else 'danger')
            top_row.addWidget(badge, 0)
            card_layout.addLayout(top_row)

            detail = QLabel(entry.detail)
            detail.setProperty('textRole', 'body')
            detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            detail.setWordWrap(True)
            card_layout.addWidget(detail)

            self.entry_layout.addWidget(card)

        self._sync_button_state()


class RunPanel(QWidget):
    """Reusable run-oriented panel for leak checks and calibration."""

    start_requested = pyqtSignal()
    retest_requested = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mode = 'calibration'
        self._result_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        header = _frame()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 18)
        header_layout.setSpacing(6)

        self.eyebrow_label = QLabel('PORT OPERATION')
        self.eyebrow_label.setProperty('role', 'eyebrow')
        header_layout.addWidget(self.eyebrow_label)

        self.title_label = _headline('Calibration')
        header_layout.addWidget(self.title_label)
        self.description_label = _body('')
        header_layout.addWidget(self.description_label)
        layout.addWidget(header)

        self.result_banner = _frame()
        self.result_banner.hide()
        banner_layout = QVBoxLayout(self.result_banner)
        banner_layout.setContentsMargins(18, 14, 18, 14)
        self.result_banner_title = QLabel('')
        self.result_banner_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_banner_title.setProperty('textRole', 'hero')
        banner_layout.addWidget(self.result_banner_title)
        self.result_banner_detail = QLabel('')
        self.result_banner_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_banner_detail.setWordWrap(True)
        self.result_banner_detail.setProperty('textRole', 'body')
        banner_layout.addWidget(self.result_banner_detail)
        layout.addWidget(self.result_banner)

        content = _frame()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 18, 20, 18)
        content_layout.setSpacing(14)

        self.status_label = QLabel('Not started.')
        self.status_label.setProperty('textRole', 'statusTitle')
        self.status_label.setWordWrap(True)
        content_layout.addWidget(self.status_label)

        self.precheck_label = QLabel('')
        self.precheck_label.setProperty('textRole', 'muted')
        self.precheck_label.setWordWrap(True)
        content_layout.addWidget(self.precheck_label)

        metric_frame = QFrame()
        metric_frame.setProperty('panelRole', 'soft')
        metric_frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        metric_layout = QGridLayout(metric_frame)
        metric_layout.setContentsMargins(14, 12, 14, 12)
        metric_layout.setHorizontalSpacing(16)
        metric_layout.setVerticalSpacing(6)
        self.metric_elapsed = QLabel('Elapsed: --')
        self.metric_mensor = QLabel('Mensor: --')
        self.metric_alicat = QLabel('Alicat: --')
        self.metric_transducer = QLabel('Transducer: --')
        for index, widget in enumerate(
            [self.metric_elapsed, self.metric_mensor, self.metric_alicat, self.metric_transducer]
        ):
            widget.setProperty('textRole', 'metric')
            metric_layout.addWidget(widget, index // 2, index % 2)
        content_layout.addWidget(metric_frame)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet(STYLES['progress_bar'])
        content_layout.addWidget(self.progress_bar)

        self.summary_label = QLabel('')
        self.summary_label.setWordWrap(True)
        self.summary_label.setProperty('textRole', 'body')
        content_layout.addWidget(self.summary_label)

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(8)
        self.results_table.setHorizontalHeaderLabels(
            [
                'Pt',
                'Target',
                'Mensor',
                'Alicat',
                'Transducer',
                'Δ (raw)',
                'Δ (corr)',
                'Result',
            ]
        )
        header = self.results_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setMinimumHeight(280)
        self.results_table.setStyleSheet(STYLES['table_widget'])
        content_layout.addWidget(self.results_table)

        self.fit_card = _frame()
        fit_layout = QVBoxLayout(self.fit_card)
        fit_layout.setContentsMargins(16, 14, 16, 14)
        fit_layout.setSpacing(6)
        fit_title = QLabel('Fit and apply')
        fit_title.setProperty('textRole', 'subsectionTitle')
        fit_layout.addWidget(fit_title)
        self.fit_summary_label = QLabel('Run calibration to generate sweep data and fit error models.')
        self.fit_summary_label.setWordWrap(True)
        self.fit_summary_label.setProperty('textRole', 'body')
        fit_layout.addWidget(self.fit_summary_label)
        self.fit_card.setVisible(True)
        content_layout.addWidget(self.fit_card)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        self.start_button = QPushButton('Start Run')
        self.start_button.setObjectName('primaryButton')
        self.start_button.clicked.connect(self.start_requested.emit)
        action_row.addWidget(self.start_button, 0)

        self.retest_spin = QSpinBox()
        self.retest_spin.setMinimum(1)
        self.retest_spin.setMaximum(1)
        self.retest_spin.setFixedWidth(90)
        action_row.addWidget(self.retest_spin, 0)

        self.retest_button = QPushButton('Retest Point')
        self.retest_button.clicked.connect(lambda: self.retest_requested.emit(self.retest_spin.value()))
        action_row.addWidget(self.retest_button, 0)
        action_row.addStretch(1)
        content_layout.addLayout(action_row)
        layout.addWidget(content)
        layout.addStretch(1)

    def configure(self, stage: WorkflowStage) -> None:
        self._mode = stage.kind
        port_label = 'Left Port' if stage.port_id == 'port_a' else 'Right Port' if stage.port_id == 'port_b' else ''
        self.eyebrow_label.setText('LEAK CHECK' if stage.kind == 'leak' else 'CALIBRATION')
        self.title_label.setText(stage.title)
        self.description_label.setText(stage.description)
        self.precheck_label.setText(
            f'{port_label} is staged and waiting for operator start.' if port_label else 'Waiting for operator start.'
        )
        self.start_button.setText('Start Leak Check' if stage.kind == 'leak' else 'Start Calibration')
        self.results_table.setVisible(stage.kind == 'calibration')
        self.retest_spin.setVisible(stage.kind == 'calibration')
        self.retest_button.setVisible(stage.kind == 'calibration')
        if stage.kind == 'leak':
            self.summary_label.hide()
        else:
            self.summary_label.show()
        self.reset()

    def reset(self) -> None:
        self._result_count = 0
        self.result_banner.hide()
        self.progress_bar.setValue(0)
        self.status_label.setText('Not started.')
        self.summary_label.setText('')
        self.summary_label.setStyleSheet(f"color: {COLORS['text_secondary']}; {TYPOGRAPHY['body']}")
        self.metric_elapsed.setText('Elapsed: --')
        self.metric_mensor.setText('Mensor: --')
        self.metric_alicat.setText('Alicat: --')
        self.metric_transducer.setText('Transducer: --')
        self.results_table.setRowCount(0)
        self.start_button.setEnabled(True)
        self.retest_spin.setEnabled(True)
        self.retest_button.setEnabled(True)
        if self._mode == 'leak':
            self.summary_label.hide()
        else:
            self.summary_label.show()

    def set_ready_message(self, message: str) -> None:
        self.precheck_label.setText(message)

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.retest_spin.setEnabled(not running)
        self.retest_button.setEnabled(not running)

    def set_progress(self, percent: int, message: str) -> None:
        self.progress_bar.setValue(percent)
        self.status_label.setText(message)

    def set_live_readings(
        self,
        *,
        elapsed_s: float | None = None,
        mensor_psia: float | None = None,
        alicat_psia: float | None = None,
        transducer_psia: float | None = None,
    ) -> None:
        self.metric_elapsed.setText(f'Elapsed: {elapsed_s:.1f} s' if elapsed_s is not None else 'Elapsed: --')
        self.metric_mensor.setText(
            f'Mensor: {mensor_psia:.3f} psia' if mensor_psia is not None else 'Mensor: --'
        )
        self.metric_alicat.setText(
            f'Alicat: {alicat_psia:.3f} psia' if alicat_psia is not None else 'Alicat: --'
        )
        self.metric_transducer.setText(
            f'Transducer: {transducer_psia:.3f} psia'
            if transducer_psia is not None
            else 'Transducer: --'
        )

    def _row_values(self, result: CalibrationPointResult) -> list[str]:
        if not result.mensor_used:
            dev = '--'
            corr = '--'
        else:
            dev = f'{result.deviation_psia:+.3f}' if result.deviation_psia is not None else '--'
            corr = (
                f'{result.corrected_deviation_psia:+.3f}'
                if result.corrected_deviation_psia is not None
                else '--'
            )
        return [
            str(result.point_index),
            f'{result.target_psia:.1f}',
            f'{result.mensor_psia:.3f}' if result.mensor_psia is not None else '--',
            f'{result.alicat_psia:.3f}' if result.alicat_psia is not None else '--',
            f'{result.transducer_psia:.3f}' if result.transducer_psia is not None else '--',
            dev,
            corr,
            'PASS' if result.passed else ('N/A' if not result.mensor_used else 'FAIL'),
        ]

    def _set_row(self, row: int, result: CalibrationPointResult) -> None:
        values = self._row_values(result)
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            if col == 7:
                if result.passed:
                    item.setForeground(QColor(COLORS['success']))
                elif result.mensor_used:
                    item.setForeground(QColor(COLORS['danger']))
            self.results_table.setItem(row, col, item)

    def set_results_table(self, results: list[CalibrationPointResult]) -> None:
        self.results_table.setRowCount(0)
        self._result_count = 0
        for result in results:
            self.append_point_result(result)

    def append_point_result(self, result: CalibrationPointResult) -> None:
        self._result_count += 1
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)
        self._set_row(row, result)
        self.summary_label.setText(f'Points completed: {self._result_count}/{result.point_total}')
        self.retest_spin.setMaximum(max(self.retest_spin.maximum(), result.point_total))

    def replace_point_result(self, result: CalibrationPointResult) -> None:
        row = max(0, result.point_index - 1)
        if row >= self.results_table.rowCount():
            self.append_point_result(result)
            return
        self._set_row(row, result)
        self.summary_label.setText(f'Point {result.point_index} retested.')

    def set_fit_summary(self, text: str, *, applied: bool | None = None) -> None:
        self.fit_summary_label.setText(text)
        if applied is True:
            self.fit_summary_label.setStyleSheet(
                f"color: {COLORS['success']}; {TYPOGRAPHY['body']} font-weight: 600;"
            )
        elif applied is False:
            self.fit_summary_label.setStyleSheet(
                f"color: {COLORS['text_secondary']}; {TYPOGRAPHY['body']}"
            )
        else:
            self.fit_summary_label.setStyleSheet(f"color: {COLORS['text_secondary']}; {TYPOGRAPHY['body']}")

    def show_calibration_result(self, results: list[CalibrationPointResult]) -> None:
        passed = bool(results) and all(result.passed for result in results)
        self._show_banner('PASS' if passed else 'FAIL', 'Calibration complete.', passed)
        self.summary_label.setText(
            f"Calibration complete. Points recorded: {len(results)}. Overall: {'PASS' if passed else 'FAIL'}."
        )
        self.set_running(False)
        self.progress_bar.setValue(100)

    def update_leak_summary(self, result: LeakCheckResult) -> None:
        status = 'PASS' if result.passed is True else 'FAIL' if result.passed is False else 'RECORDED'
        detail = f'Alicat leak rate: {result.alicat_leak_rate_psi_per_min:.4f} psi/min'
        self._show_banner(status, detail, result.passed)
        transducer_text = (
            f'{result.transducer_leak_rate_psi_per_min:.4f} psi/min'
            if result.transducer_leak_rate_psi_per_min is not None
            else '--'
        )
        self.summary_label.setText(
            f'Alicat leak rate: {result.alicat_leak_rate_psi_per_min:.4f} psi/min\n'
            f'Transducer leak rate: {transducer_text}'
        )
        self.set_running(False)
        self.progress_bar.setValue(100)

    def show_error(self, message: str) -> None:
        self.status_label.setText('Run failed.')
        self.summary_label.setText(message)
        self.summary_label.setStyleSheet(
            f"color: {COLORS['danger']}; {TYPOGRAPHY['body']} font-weight: 700;"
        )
        self.set_running(False)

    def _show_banner(self, title: str, detail: str, passed: bool | None) -> None:
        self.result_banner.show()
        self.result_banner_title.setText(title)
        self.result_banner_detail.setText(detail)
        state = 'neutral'
        if passed is True:
            state = 'success'
        elif passed is False:
            state = 'danger'
        self.result_banner.setProperty('bannerState', state)
        self.result_banner.style().unpolish(self.result_banner)
        self.result_banner.style().polish(self.result_banner)


class MoveMensorPanel(QWidget):
    """Intermediate stage for physically moving the Mensor."""

    confirm_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        card = _frame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(12)
        card_layout.addWidget(_headline('Move the Mensor to the right port'))
        card_layout.addWidget(
            _body(
                'Disconnect the Mensor from the left port, move it to the right port, verify the fitting is snug, and then confirm the station is ready.'
            )
        )

        self.port_label = QLabel('Mensor COM port: --')
        self.port_label.setProperty('textRole', 'muted')
        card_layout.addWidget(self.port_label)

        row = QHBoxLayout()
        row.addStretch(1)
        self.confirm_button = QPushButton('Confirm Mensor Move')
        self.confirm_button.setObjectName('primaryButton')
        self.confirm_button.clicked.connect(self.confirm_requested.emit)
        row.addWidget(self.confirm_button)
        card_layout.addLayout(row)
        layout.addWidget(card)
        layout.addStretch(1)

    def set_port_text(self, port: str) -> None:
        display = port.strip() if port else '--'
        self.port_label.setText(f'Mensor COM port: {display}')


class ReportPanel(QWidget):
    """Final report screen with preview and export actions."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._session: QualityCalibrationSession | None = None
        self._settings: QualitySettings | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        self.banner = _frame()
        banner_layout = QVBoxLayout(self.banner)
        banner_layout.setContentsMargins(24, 20, 24, 20)
        banner_layout.setSpacing(8)
        self.banner_title = QLabel('PASS')
        self.banner_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner_title.setProperty('textRole', 'hero')
        banner_layout.addWidget(self.banner_title)
        self.banner_meta = QLabel('')
        self.banner_meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner_meta.setWordWrap(True)
        self.banner_meta.setProperty('textRole', 'body')
        banner_layout.addWidget(self.banner_meta)
        layout.addWidget(self.banner)

        meta_card = _frame()
        meta_layout = QVBoxLayout(meta_card)
        meta_layout.setContentsMargins(24, 20, 24, 20)
        meta_layout.setSpacing(8)
        self.summary_label = QLabel('')
        self.summary_label.setWordWrap(True)
        self.summary_label.setProperty('textRole', 'body')
        meta_layout.addWidget(self.summary_label)
        layout.addWidget(meta_card)

        self.browser = QTextBrowser()
        self.browser.setStyleSheet(STYLES['readonly_text_edit'])
        self.browser.setMinimumHeight(420)
        layout.addWidget(self.browser, 1)

        action_card = _frame()
        action_layout = QVBoxLayout(action_card)
        action_layout.setContentsMargins(24, 20, 24, 20)
        row = QHBoxLayout()
        row.setSpacing(12)

        self.save_button = QPushButton('Save PDF...')
        self.save_button.clicked.connect(self._save_pdf)
        row.addWidget(self.save_button)
        self.export_button = QPushButton('Export CSV...')
        self.export_button.clicked.connect(self._export_csv)
        row.addWidget(self.export_button)
        self.print_button = QPushButton('Print')
        self.print_button.clicked.connect(self._print_report)
        row.addWidget(self.print_button)
        self.open_button = QPushButton('Open Output Folder')
        self.open_button.clicked.connect(self._open_output_folder)
        row.addWidget(self.open_button)
        row.addStretch(1)
        action_layout.addLayout(row)

        self.saved_label = QLabel('')
        self.saved_label.setProperty('textRole', 'muted')
        self.saved_label.setWordWrap(True)
        action_layout.addWidget(self.saved_label)
        layout.addWidget(action_card)

    def render(self, session: QualityCalibrationSession, settings: QualitySettings) -> None:
        self._session = session
        self._settings = settings
        self.browser.setHtml(build_report_html(session, settings))
        self.summary_label.setText(
            f'Desktop reports: {settings.desktop_output_dir}\n'
            f'Records folder: {settings.report_output_dir}\n'
            f'Template: {settings.report_template_path}'
        )
        started = session.started_at.strftime('%Y-%m-%d %H:%M') if session.started_at else '--'
        completed = session.completed_at.strftime('%Y-%m-%d %H:%M') if session.completed_at else '--'
        self.banner_meta.setText(
            f'Technician: {session.technician_name or "--"}  |  '
            f'Asset: {session.asset_id or "--"}  |  '
            f'Started: {started}  |  Completed: {completed}'
        )
        passed = session.overall_passed
        self.banner_title.setText('PASS' if passed else 'FAIL')
        self.banner.setProperty('bannerState', 'success' if passed else 'danger')
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        cert_docx = getattr(session, 'last_certificate_docx', None)
        cert_pdf = getattr(session, 'last_certificate_pdf', None)
        if cert_docx or cert_pdf:
            parts = ['Certificates saved:']
            if cert_docx:
                parts.append(f'  DOCX: {cert_docx}')
            if cert_pdf:
                parts.append(f'  PDF:  {cert_pdf}')
            self.saved_label.setText('\n'.join(parts))
        else:
            self.saved_label.setText('')

    def _save_pdf(self) -> None:
        if self._session is None or self._settings is None:
            return
        start_path = self._settings.report_output_dir / default_report_filename(self._session, self._settings)
        path, _ = QFileDialog.getSaveFileName(self, 'Save PDF Report', str(start_path), 'PDF (*.pdf)')
        if not path:
            return
        try:
            out_path = export_report_pdf(self._session, self._settings, Path(path))
            self._session.last_report_path = out_path
            self.saved_label.setText(f'Saved successfully:\n{out_path}')
        except Exception as exc:
            QMessageBox.critical(self, 'Save Failed', str(exc))

    def _export_csv(self) -> None:
        if self._session is None or self._settings is None:
            return
        start_path = self._settings.report_output_dir / default_csv_filename(self._session, self._settings)
        path, _ = QFileDialog.getSaveFileName(self, 'Export Calibration Data (CSV)', str(start_path), 'CSV (*.csv)')
        if not path:
            return
        try:
            out_path = export_report_csv(self._session, self._settings, Path(path))
            self.saved_label.setText(f'Exported successfully:\n{out_path}')
        except Exception as exc:
            QMessageBox.critical(self, 'Export Failed', str(exc))

    def _print_report(self) -> None:
        if self._session is None or self._settings is None:
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec():
            document = build_text_document(self._session, self._settings)
            document.print(printer)

    def _open_output_folder(self) -> None:
        if self._settings is None:
            return
        folder = self._settings.desktop_output_dir
        if not folder.is_dir():
            folder = self._settings.report_output_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
