"""
Port column widget for Stinger main window.

Provides a single test port column with serial control, pressure readout,
result display, and action buttons. Includes ClickableLabel and
TouchKeypadDialog used only by this widget.
"""

from typing import Any, Dict, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from app.ui.styles import STYLES
from app.ui.widgets import PressureBarWidget, LEDIndicator


class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, ev) -> None:
        if ev and ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)


class TouchKeypadDialog(QDialog):
    """Touch-friendly numeric keypad dialog."""

    def __init__(self, parent: QWidget, initial_value: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Enter Serial Number")
        self.setModal(True)
        self.setMinimumSize(360, 420)

        self._value = initial_value

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self._display = QLineEdit()
        self._display.setText(self._value)
        self._display.setReadOnly(True)
        self._display.setMinimumHeight(50)
        self._display.setStyleSheet("font-size: 20px; padding: 8px;")
        layout.addWidget(self._display)

        grid = QGridLayout()
        grid.setSpacing(8)

        buttons = [
            ("7", 0, 0), ("8", 0, 1), ("9", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("1", 2, 0), ("2", 2, 1), ("3", 2, 2),
            ("0", 3, 1),
        ]
        for text, row, col in buttons:
            btn = QPushButton(text)
            btn.setMinimumSize(90, 60)
            btn.setStyleSheet("font-size: 18px; font-weight: bold;")
            btn.clicked.connect(lambda checked=False, t=text: self._append_digit(t))
            grid.addWidget(btn, row, col)

        btn_clear = QPushButton("Clear")
        btn_clear.setMinimumSize(90, 60)
        btn_clear.clicked.connect(self._clear)
        grid.addWidget(btn_clear, 3, 0)

        btn_back = QPushButton("Back")
        btn_back.setMinimumSize(90, 60)
        btn_back.clicked.connect(self._backspace)
        grid.addWidget(btn_back, 3, 2)

        layout.addLayout(grid)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setMinimumHeight(50)
        btn_cancel.clicked.connect(self.reject)
        actions.addWidget(btn_cancel)

        btn_ok = QPushButton("OK")
        btn_ok.setMinimumHeight(50)
        btn_ok.setStyleSheet("font-weight: bold;")
        btn_ok.clicked.connect(self.accept)
        actions.addWidget(btn_ok)

        layout.addLayout(actions)

    def _append_digit(self, digit: str) -> None:
        self._value += digit
        self._display.setText(self._value)

    def _clear(self) -> None:
        self._value = ""
        self._display.setText(self._value)

    def _backspace(self) -> None:
        self._value = self._value[:-1]
        self._display.setText(self._value)

    def value(self) -> str:
        return self._value


class PortColumn(QFrame):
    """
    Widget for a single test port column.

    Displays:
    - Serial number control
    - Pressure readout + visualization
    - Acceptance bands
    - Result display
    - Action buttons
    """

    action_requested = pyqtSignal(str, str)
    serial_increment_requested = pyqtSignal(str)
    serial_decrement_requested = pyqtSignal(str)
    serial_manual_entry_requested = pyqtSignal(str, int)

    def __init__(self, port_id: str, title: str):
        """
        Initialize port column.

        Args:
            title: Display title for this port.
        """
        super().__init__()
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setObjectName('portColumnCard')

        self._port_id = port_id
        self._title = title
        self._primary_action: Optional[str] = None
        self._cancel_action: Optional[str] = None
        self._blink_timer: Optional[QTimer] = None
        self._blink_on = True
        self._blink_active = False
        self._primary_base_style = ""
        self._no_indicator: Optional[LEDIndicator] = None
        self._nc_indicator: Optional[LEDIndicator] = None
        self._no_active = False
        self._nc_active = False
        self._state_machine_enabled = False
        self._last_color = "default"
        self._primary_requested_label = ""
        self._switch_sample_received = False
        self._result_state: Optional[str] = None
        self._setup_ui()
        self._apply_card_status_style('default')

    def _setup_ui(self) -> None:
        """Build the port column layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(10, 10, 10, 10)

        # Header row with three zones: left (title), center (pills), right (serial)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(2, 1, 2, 1)
        header_layout.setSpacing(8)

        # Left zone: Title only
        title_lbl = QLabel(self._title)
        title_lbl.setFont(QFont("Segoe UI, Inter, Arial", 16, QFont.Weight.Bold))
        title_lbl.setStyleSheet("color: #1a1a2e;")
        header_layout.addWidget(title_lbl)

        header_layout.addStretch()

        # Center zone: Pills
        pills_layout = QHBoxLayout()
        pills_layout.setSpacing(8)

        # Combined ACT/DEACT pill (no separate Result pill — space is tight)
        self._pill_act_deact = self._build_combined_act_deact_pill()

        pills_layout.addWidget(self._pill_act_deact)

        header_layout.addLayout(pills_layout)
        header_layout.addStretch()

        # Right zone: Serial (far right) - pill-shaped container
        serial_group = QFrame()
        serial_group.setStyleSheet(STYLES["serial_group_shell"])
        serial_layout = QHBoxLayout(serial_group)
        serial_layout.setContentsMargins(8, 4, 8, 4)
        serial_layout.setSpacing(12)

        # Circular buttons
        self._btn_serial_dec = QPushButton("-")
        self._btn_serial_dec.setFixedSize(36, 36)
        self._btn_serial_dec.setFont(QFont("Segoe UI, Inter, Arial", 14, QFont.Weight.Bold))
        self._btn_serial_dec.setStyleSheet(STYLES["serial_stepper_button"])
        serial_layout.addWidget(self._btn_serial_dec)

        serial_label = QLabel("SN")
        serial_label.setStyleSheet("color: #6b7280; font-size: 11px; font-weight: bold;")
        serial_layout.addWidget(serial_label)

        self._lbl_serial = ClickableLabel("---")
        self._lbl_serial.setFont(QFont("Consolas, Courier New, monospace", 16, QFont.Weight.Bold))
        self._lbl_serial.setStyleSheet("color: #1a1a2e; background: transparent;")
        self._lbl_serial.clicked.connect(self._on_serial_clicked)
        serial_layout.addWidget(self._lbl_serial)

        self._btn_serial_inc = QPushButton("+")
        self._btn_serial_inc.setFixedSize(36, 36)
        self._btn_serial_inc.setFont(QFont("Segoe UI, Inter, Arial", 14, QFont.Weight.Bold))
        self._btn_serial_inc.setStyleSheet(STYLES["serial_stepper_button"])
        serial_layout.addWidget(self._btn_serial_inc)

        self._btn_serial_dec.clicked.connect(self._on_serial_decrement)
        self._btn_serial_inc.clicked.connect(self._on_serial_increment)

        header_layout.addWidget(serial_group)

        layout.addLayout(header_layout)

        # Pressure display - inline value and unit with monospace digits
        pressure_container = QWidget()
        pressure_layout = QHBoxLayout(pressure_container)
        pressure_layout.setContentsMargins(4, 8, 4, 2)
        pressure_layout.setSpacing(10)

        pressure_layout.addStretch()

        # Inline pressure value and unit
        self._lbl_pressure = QLabel("0.00")
        self._lbl_pressure.setFont(QFont("Consolas, Courier New, monospace", 32, QFont.Weight.Bold))
        self._lbl_pressure.setStyleSheet("color: #1a1a2e;")
        self._lbl_pressure.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # Set fixed width to prevent jumping when minus sign appears
        self._lbl_pressure.setMinimumWidth(120)
        self._lbl_pressure.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        pressure_layout.addWidget(self._lbl_pressure)

        self._lbl_pressure_unit = QLabel("PSI")
        self._lbl_pressure_unit.setFont(QFont("Segoe UI, Inter, Arial", 16))
        self._lbl_pressure_unit.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._lbl_pressure_unit.setStyleSheet("color: #6b7280; padding: 2px 0 0 4px;")
        pressure_layout.addWidget(self._lbl_pressure_unit)

        pressure_layout.addStretch()

        # Compact switch indicators on the right
        switch_widget, no_indicator, nc_indicator = self._build_switch_indicator_group()
        self._no_indicator = no_indicator
        self._nc_indicator = nc_indicator
        pressure_layout.addWidget(switch_widget)

        pressure_layout.addStretch()

        layout.addWidget(pressure_container)

        # Pressure bar visualization
        self._pressure_bar = PressureBarWidget()
        self._pressure_bar.set_axis_side('right' if self._port_id == 'port_a' else 'left')
        self._pressure_bar.setMinimumHeight(340)
        self._pressure_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Action buttons - refined with better styling
        button_frame = QFrame()
        button_frame.setContentsMargins(0, 0, 0, 0)
        button_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        button_layout = QVBoxLayout(button_frame)
        button_layout.setContentsMargins(2, 2, 2, 2)
        button_layout.setSpacing(6)

        self._btn_cancel = QPushButton("Vent")
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setFont(QFont("Segoe UI, Inter, Arial", 13, QFont.Weight.Bold))

        self._btn_primary = QPushButton("Pressurize")
        self._btn_primary.setFont(QFont("Segoe UI, Inter, Arial", 15, QFont.Weight.Bold))

        self._btn_cancel.setMinimumHeight(60)
        self._btn_primary.setMinimumHeight(120)
        self._btn_cancel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._btn_primary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        base_button_width = max(self._btn_primary.sizeHint().width(), self._btn_cancel.sizeHint().width())
        target_button_width = int(base_button_width * 1.25)
        self._btn_cancel.setMinimumWidth(target_button_width)
        self._btn_primary.setMinimumWidth(target_button_width)

        # Keep cancel at the top and make primary occupy the lower 3/4.
        button_layout.addWidget(self._btn_cancel, 1)
        button_layout.addWidget(self._btn_primary, 3)

        self._btn_primary.clicked.connect(self._on_primary_clicked)
        self._btn_cancel.clicked.connect(self._on_cancel_clicked)

        # Keep button stack on the outer edge for each port.
        graph_button_container = QWidget()
        graph_button_layout = QHBoxLayout(graph_button_container)
        graph_button_layout.setContentsMargins(0, 0, 0, 0)
        graph_button_layout.setSpacing(2)

        if self._port_id == "port_a":
            graph_button_layout.addWidget(button_frame, 1)
            graph_button_layout.addWidget(self._pressure_bar, 1)
        else:
            graph_button_layout.addWidget(self._pressure_bar, 1)
            graph_button_layout.addWidget(button_frame, 1)

        layout.addWidget(graph_button_container, 1)
        layout.setStretch(0, 0)
        layout.setStretch(1, 0)
        layout.setStretch(2, 1)
        self._apply_cancel_style(False)

    def _build_pill(self, title: str, value: str, bold: bool = False, color_hint: Optional[str] = None) -> QFrame:
        pill = QFrame()
        pill.setMinimumWidth(96)
        pill.setFixedHeight(48)  # Consistent height

        # Remove pill shape styling - no border, no background
        pill.setStyleSheet("background-color: transparent;")
        layout = QVBoxLayout(pill)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(2)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("color: #6b7280; font-size: 11px;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_lbl)

        value_lbl = QLabel(value)
        weight = "bold" if bold else "normal"
        value_lbl.setStyleSheet(f"color: #1a1a2e; font-size: 14px; font-weight: {weight};")
        value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(value_lbl)

        pill.setProperty("value_label", value_lbl)
        return pill

    def _build_combined_act_deact_pill(self) -> QFrame:
        """Build compact ACT/DEACT display container."""
        pill = QFrame()
        pill.setMinimumWidth(280)
        pill.setFixedHeight(54)
        pill.setStyleSheet(STYLES["compact_panel_shell"])

        layout = QVBoxLayout(pill)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)

        # Values container (ACT and DEACT side by side)
        values_container = QWidget()
        values_layout = QHBoxLayout(values_container)
        values_layout.setContentsMargins(0, 0, 0, 0)
        values_layout.setSpacing(16)
        
        # ACT column
        act_container = QWidget()
        act_layout = QVBoxLayout(act_container)
        act_layout.setContentsMargins(0, 0, 0, 0)
        act_layout.setSpacing(2)
        
        act_title = QLabel("ACT")
        act_title.setStyleSheet("color: #6b7280; font-size: 10px; font-weight: bold;")
        act_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        act_layout.addWidget(act_title)
        
        act_value = QLabel("---")
        act_value.setFont(QFont("Consolas, Courier New, monospace", 13, QFont.Weight.Bold))
        act_value.setStyleSheet("color: #1a1a2e;")
        act_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        act_layout.addWidget(act_value)
        
        values_layout.addWidget(act_container)
        
        # DEACT column
        deact_container = QWidget()
        deact_layout = QVBoxLayout(deact_container)
        deact_layout.setContentsMargins(0, 0, 0, 0)
        deact_layout.setSpacing(2)
        
        deact_title = QLabel("DEACT")
        deact_title.setStyleSheet("color: #6b7280; font-size: 10px; font-weight: bold;")
        deact_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        deact_layout.addWidget(deact_title)
        
        deact_value = QLabel("---")
        deact_value.setFont(QFont("Consolas, Courier New, monospace", 13, QFont.Weight.Bold))
        deact_value.setStyleSheet("color: #1a1a2e;")
        deact_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        deact_layout.addWidget(deact_value)
        
        values_layout.addWidget(deact_container)
        
        layout.addWidget(values_container)

        pill.setProperty("act_title", act_title)
        pill.setProperty("deact_title", deact_title)
        pill.setProperty("act_value", act_value)
        pill.setProperty("deact_value", deact_value)
        return pill

    def _build_switch_indicator_group(self) -> Tuple[QWidget, LEDIndicator, LEDIndicator]:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        row = QHBoxLayout()
        row.setSpacing(10)
        no_row, no_indicator = self._build_led_indicator('NO')
        nc_row, nc_indicator = self._build_led_indicator('NC')
        row.addLayout(no_row)
        row.addLayout(nc_row)
        row.addStretch()
        layout.addLayout(row)

        return container, no_indicator, nc_indicator

    def _build_led_indicator(self, label: str) -> tuple[QHBoxLayout, LEDIndicator]:
        row = QHBoxLayout()
        row.setSpacing(6)

        text = QLabel(label)
        text.setStyleSheet('color: #4b5563; font-size: 10px; font-weight: bold;')

        led_size = 32
        led = LEDIndicator(size=led_size)
        led.set_nc_mode(label == 'NC')
        led.set_active(False)

        row.addWidget(text)
        row.addWidget(led)

        return row, led

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    def set_serial(self, serial: int) -> None:
        """Set the displayed serial number."""
        self._lbl_serial.setText(str(serial))

    def get_serial_text(self) -> str:
        """Get the displayed serial number text."""
        return self._lbl_serial.text()

    def set_pressure(self, pressure: float, unit: str = "PSI") -> None:
        """Set the displayed pressure value."""
        self._lbl_pressure.setText(f"{pressure:.2f}")
        self._lbl_pressure_unit.setText(unit)
        self._pressure_bar.set_pressure(pressure)

    def set_result(
        self,
        activation: Optional[float],
        deactivation: Optional[float],
        in_spec: Optional[bool]
    ) -> None:
        """Set the result display."""
        self._set_act_deact_values(activation, deactivation)

        if in_spec is None:
            self._result_state = None
            self._apply_card_status_style('default')
        elif in_spec:
            self._result_state = 'pass'
            self._apply_card_status_style('pass')
        else:
            self._result_state = 'fail'
            self._apply_card_status_style('fail')

        self._pressure_bar.set_measured_points(activation, deactivation)

    def reset_test_display(self) -> None:
        """Clear per-test result markers while keeping live pressure/switch state."""
        self._result_state = None
        self._set_act_deact_values(None, None)
        self._pressure_bar.set_measured_points(None, None)
        self._pressure_bar.set_estimated_points(None, None, 0)
        self._apply_card_status_style('default')

    def set_switch_state(self, no_active: bool, nc_active: bool) -> None:
        self._no_active = no_active
        self._nc_active = nc_active
        self._switch_sample_received = True
        no_indicator = self._no_indicator
        nc_indicator = self._nc_indicator
        if no_indicator is not None:
            no_indicator.set_active(no_active)
        if nc_indicator is not None:
            nc_indicator.set_active(nc_active)
        # Update pressurize button state based on switch connection
        self._update_pressurize_button_state()

    def set_button_state(self, primary: Dict, cancel: Dict) -> None:
        """Set the button states."""
        primary_label = primary.get('label', '')
        self._primary_requested_label = primary_label
        self._btn_primary.setText(primary_label)
        # Store the original enabled state from state machine
        self._state_machine_enabled = primary.get('enabled', False)
        self._primary_action = primary.get('action')

        color = primary.get('color', 'default')
        self._last_color = color
        blink = bool(primary.get('blink', False))
        self._set_blinking(blink)
        self._update_card_status_for_buttons(primary_label)

        # For switch-gated actions, style/enabled state is handled below.
        if primary_label not in {'Pressurize', 'Test'}:
            self._apply_primary_style(color)
            self._btn_primary.setEnabled(self._state_machine_enabled)
        else:
            # Will be handled by _update_pressurize_button_state
            pass

        self._btn_cancel.setText(cancel.get('label', ''))
        self._btn_cancel.setEnabled(cancel.get('enabled', False))
        self._cancel_action = cancel.get('action')
        self._apply_cancel_style(self._btn_cancel.isEnabled())

        # Update pressurize button state based on switch connection
        self._update_pressurize_button_state()

    def set_pressure_visualization(self, viz_data: Dict[str, Any]) -> None:
        """Update pressure bar configuration."""
        if not viz_data:
            self._pressure_bar.reset_visualization()
            self._set_act_deact_labels('ACT', 'DEACT')
            self._set_act_deact_values(None, None)
            return

        self._set_act_deact_labels(
            viz_data.get('activation_label'),
            viz_data.get('deactivation_label'),
        )
        self._pressure_bar.set_point_labels(
            viz_data.get('activation_label'),
            viz_data.get('deactivation_label'),
        )

        min_psi = viz_data.get('min_psi')
        max_psi = viz_data.get('max_psi')
        if min_psi is not None and max_psi is not None:
            self._pressure_bar.set_scale(min_psi, max_psi)

        # Sync the pressure bar axis label with the current display unit
        unit_text = self._lbl_pressure_unit.text()
        if unit_text:
            self._pressure_bar.set_units_label(unit_text)

        if 'activation_band' in viz_data or 'deactivation_band' in viz_data:
            activation_band = viz_data.get('activation_band')
            deactivation_band = viz_data.get('deactivation_band')
            self._pressure_bar.set_bands(activation_band, deactivation_band)

        atmosphere_psi = viz_data.get('atmosphere_psi')
        if atmosphere_psi is not None:
            self._pressure_bar.set_atmosphere_pressure(atmosphere_psi)

        measured_activation: Optional[float] = None
        measured_deactivation: Optional[float] = None

        if 'measured_activation' in viz_data or 'measured_deactivation' in viz_data:
            measured_activation = viz_data.get('measured_activation')
            measured_deactivation = viz_data.get('measured_deactivation')
            self._pressure_bar.set_measured_points(measured_activation, measured_deactivation)
            self._set_act_deact_values(measured_activation, measured_deactivation)

        if (
            'estimated_activation' in viz_data
            or 'estimated_deactivation' in viz_data
            or 'estimated_sample_count' in viz_data
        ):
            estimated_activation = viz_data.get('estimated_activation')
            estimated_deactivation = viz_data.get('estimated_deactivation')
            estimated_count = int(viz_data.get('estimated_sample_count') or 0)
            self._pressure_bar.set_estimated_points(
                estimated_activation,
                estimated_deactivation,
                estimated_count,
            )
            if measured_activation is None and measured_deactivation is None:
                self._set_act_deact_values(estimated_activation, estimated_deactivation)

        show_atm = viz_data.get('show_atmosphere_reference', True)
        show_bands = viz_data.get('show_acceptance_bands', True)
        show_points = viz_data.get('show_measured_points', True)
        self._pressure_bar.set_display_flags(show_atm, show_bands, show_points)

    def _set_pill_value(self, pill: QFrame, value: str) -> None:
        value_label = pill.property("value_label")
        if isinstance(value_label, QLabel):
            value_label.setText(value)

    def _set_act_deact_labels(
        self,
        activation_label: Optional[str],
        deactivation_label: Optional[str],
    ) -> None:
        act_title = self._pill_act_deact.property("act_title")
        deact_title = self._pill_act_deact.property("deact_title")
        if isinstance(act_title, QLabel) and activation_label:
            act_title.setText(str(activation_label))
        if isinstance(deact_title, QLabel) and deactivation_label:
            deact_title.setText(str(deactivation_label))

    def _set_act_deact_values(
        self,
        activation: Optional[float],
        deactivation: Optional[float],
    ) -> None:
        act_text = f"{activation:.2f}" if activation is not None else "---"
        deact_text = f"{deactivation:.2f}" if deactivation is not None else "---"

        act_value = self._pill_act_deact.property("act_value")
        deact_value = self._pill_act_deact.property("deact_value")
        if isinstance(act_value, QLabel):
            act_value.setText(act_text)
        if isinstance(deact_value, QLabel):
            deact_value.setText(deact_text)

    def _update_card_status_for_buttons(self, primary_label: str) -> None:
        if primary_label in {'Cycling…', 'Testing…', 'Pressurizing…'}:
            self._apply_card_status_style('testing')
            return
        if self._result_state == 'pass':
            self._apply_card_status_style('pass')
            return
        if self._result_state == 'fail':
            self._apply_card_status_style('fail')
            return
        self._apply_card_status_style('default')

    def _apply_card_status_style(self, status: str) -> None:
        styles = {
            'default': (
                '#ffffff',
                'rgba(0, 0, 0, 0.08)',
            ),
            'testing': (
                'rgba(245, 158, 11, 0.12)',
                'rgba(217, 119, 6, 0.36)',
            ),
            'pass': (
                'rgba(34, 197, 94, 0.14)',
                'rgba(22, 163, 74, 0.36)',
            ),
            'fail': (
                'rgba(220, 38, 38, 0.12)',
                'rgba(185, 28, 28, 0.34)',
            ),
        }
        background, border = styles.get(status, styles['default'])
        self.setStyleSheet(
            '#portColumnCard {'
            f'background-color: {background}; '
            f'border: 1px solid {border}; '
            'border-radius: 12px;'
            '}'
        )

    def _set_result_pill_style(self, state: str) -> None:
        """Kept for backward-compatibility; result pill has been removed."""
        pass

    def _update_pressurize_button_state(self) -> None:
        """Gate Pressurize/Test actions on detected switch presence."""
        if self._primary_requested_label not in {'Pressurize', 'Test'}:
            return

        if not self._switch_sample_received:
            self._btn_primary.setText('Hardware Connecting...')
            self._btn_primary.setEnabled(False)
            self._apply_primary_style('default')
            return

        switch_connected = self._no_active or self._nc_active
        final_enabled = self._state_machine_enabled and switch_connected
        self._btn_primary.setEnabled(final_enabled)

        if final_enabled:
            self._btn_primary.setText(self._primary_requested_label)
            # If the state machine left the color as the inactive default, promote
            # it to green so the button clearly signals it is ready to press.
            active_color = self._last_color if self._last_color != 'default' else 'green'
            self._apply_primary_style(active_color)
        else:
            if self._state_machine_enabled and not switch_connected:
                self._btn_primary.setText('Waiting for Switch...')
            else:
                self._btn_primary.setText(self._primary_requested_label)
            self._apply_primary_style('default')

    def _apply_primary_style(self, color: str) -> None:
        styles = {
            "green": """
                QPushButton {
                    background-color: #16a34a;
                    color: white;
                    font-weight: bold;
                    border: 1px solid rgba(0, 0, 0, 0.10);
                    border-radius: 8px;
                }
                QPushButton:hover {
                    background-color: #15803d;
                }
                QPushButton:pressed {
                    background-color: #166534;
                }
            """,
            "yellow": """
                QPushButton {
                    background-color: #d97706;
                    color: white;
                    font-weight: bold;
                    border: 1px solid rgba(0, 0, 0, 0.10);
                    border-radius: 8px;
                }
                QPushButton:hover {
                    background-color: #b45309;
                }
                QPushButton:pressed {
                    background-color: #92400e;
                }
            """,
            "blue": """
                QPushButton {
                    background-color: #2563eb;
                    color: white;
                    font-weight: bold;
                    border: 1px solid rgba(0, 0, 0, 0.10);
                    border-radius: 8px;
                }
                QPushButton:hover {
                    background-color: #1d4ed8;
                }
                QPushButton:pressed {
                    background-color: #1e40af;
                }
            """,
            "red": """
                QPushButton {
                    background-color: #dc2626;
                    color: white;
                    font-weight: bold;
                    border: 1px solid rgba(0, 0, 0, 0.10);
                    border-radius: 8px;
                }
                QPushButton:hover {
                    background-color: #b91c1c;
                }
                QPushButton:pressed {
                    background-color: #991b1b;
                }
            """,
            "default": """
                QPushButton {
                    background-color: #e5e7eb;
                    color: #6b7280;
                    border: 1px solid rgba(0, 0, 0, 0.08);
                    border-radius: 8px;
                }
            """,
        }
        self._last_color = color
        self._primary_base_style = styles.get(color, styles["default"])
        self._btn_primary.setStyleSheet(self._primary_base_style)

    def _apply_cancel_style(self, enabled: bool) -> None:
        if enabled:
            self._btn_cancel.setStyleSheet("""
                QPushButton {
                    background-color: #dc2626;
                    color: white;
                    font-weight: bold;
                    border: 1px solid rgba(0, 0, 0, 0.10);
                    border-radius: 8px;
                }
                QPushButton:hover {
                    background-color: #b91c1c;
                }
                QPushButton:pressed {
                    background-color: #991b1b;
                }
            """)
        else:
            self._btn_cancel.setStyleSheet("""
                QPushButton:disabled {
                    background-color: #f3f4f6;
                    color: #9ca3af;
                    border: 1px solid rgba(0, 0, 0, 0.06);
                    border-radius: 8px;
                }
            """)

    def _set_blinking(self, enabled: bool) -> None:
        if enabled == self._blink_active:
            return
        self._blink_active = enabled
        if enabled:
            if self._blink_timer is None:
                self._blink_timer = QTimer(self)
                self._blink_timer.timeout.connect(self._toggle_blink)
            self._blink_on = True
            self._blink_timer.start(450)
        else:
            if self._blink_timer:
                self._blink_timer.stop()
            self._btn_primary.setStyleSheet(self._primary_base_style)

    def _toggle_blink(self) -> None:
        if not self._blink_active:
            return
        self._blink_on = not self._blink_on
        if self._blink_on:
            self._btn_primary.setStyleSheet(self._primary_base_style)
        else:
            # Smoother blink using border emphasis
            if "#16a34a" in self._primary_base_style:
                blink_style = """
                    QPushButton {
                        background-color: #16a34a;
                        color: white;
                        font-weight: bold;
                        border: 2px solid rgba(0, 0, 0, 0.35);
                        border-radius: 8px;
                    }
                """
            else:
                blink_style = self._primary_base_style.replace(
                    "border: 1px solid", "border: 2px solid"
                ).replace("0.10)", "0.35)")
            self._btn_primary.setStyleSheet(blink_style)

    def _on_primary_clicked(self) -> None:
        if self._primary_action:
            self.action_requested.emit(self._port_id, self._primary_action)

    def _on_cancel_clicked(self) -> None:
        if self._cancel_action:
            self.action_requested.emit(self._port_id, self._cancel_action)

    def _on_serial_increment(self) -> None:
        self.serial_increment_requested.emit(self._port_id)

    def _on_serial_decrement(self) -> None:
        self.serial_decrement_requested.emit(self._port_id)

    def _on_serial_clicked(self) -> None:
        dialog = TouchKeypadDialog(self, self._lbl_serial.text())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            value = dialog.value().strip()
            if value.isdigit():
                self.serial_manual_entry_requested.emit(self._port_id, int(value))
