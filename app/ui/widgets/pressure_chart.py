"""
Real-time pressure chart widget using pyqtgraph.
"""
import logging
from collections import deque
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget, QVBoxLayout

logger = logging.getLogger(__name__)


class PressureChartWidget(QWidget):
    """Widget for displaying real-time pressure data with transducer and setpoint traces."""
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        # Data buffers (rolling window)
        self._max_points = 600  # 60 seconds at 10 Hz
        self._times = deque(maxlen=self._max_points)
        self._transducer_pressures = deque(maxlen=self._max_points)
        self._setpoints = deque(maxlen=self._max_points)
        self._alicat_pressures = deque(maxlen=self._max_points)
        self._start_time: Optional[float] = None
        self._last_transducer: Optional[float] = None
        self._last_setpoint: Optional[float] = None
        self._last_alicat: Optional[float] = None
        
        # Update throttling - limit to 20Hz to reduce CPU usage
        self._update_pending = False
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.timeout.connect(self._do_update_plot)
        self._update_interval_ms = 50  # 50ms = 20Hz
        
        # Setup UI
        self._setup_ui()
        
    def _setup_ui(self) -> None:
        """Initialize the chart UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Create plot widget
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground('#f5f6f8')
        self._plot_widget.showGrid(x=True, y=True, alpha=0.3)
        
        # Configure axes
        self._plot_widget.setLabel('left', 'Pressure', units='PSI')
        self._plot_widget.setLabel('bottom', 'Time', units='s')
        
        # Create plot lines
        # Transducer pressure (dark solid line)
        self._transducer_line = self._plot_widget.plot(
            pen=pg.mkPen(color='#1a1a2e', width=2),
            name='Transducer'
        )
        
        # Alicat setpoint (blue dashed line)
        self._setpoint_line = self._plot_widget.plot(
            pen=pg.mkPen(color='#2563eb', width=2, style=pg.QtCore.Qt.PenStyle.DashLine),
            name='Setpoint'
        )

        # Alicat pressure (amber solid line)
        self._alicat_line = self._plot_widget.plot(
            pen=pg.mkPen(color='#f59e0b', width=2),
            name='Alicat'
        )
        
        # Add legend
        self._plot_widget.addLegend(offset=(10, 10))
        
        layout.addWidget(self._plot_widget)
        
    def add_data_point(
        self, 
        timestamp: float, 
        transducer_pressure: Optional[float],
        setpoint: Optional[float],
        alicat_pressure: Optional[float] = None
    ) -> None:
        """
        Add a new data point to the chart.
        
        Args:
            timestamp: Unix timestamp of the reading
            transducer_pressure: Transducer reading in PSI (or None if unavailable)
            setpoint: Alicat setpoint in PSI (or None if unavailable)
        """
        # Initialize start time on first data point
        if self._start_time is None:
            self._start_time = timestamp
        
        # Calculate relative time
        relative_time = timestamp - self._start_time
        
        # Add to buffers
        self._times.append(relative_time)
        if transducer_pressure is not None:
            self._last_transducer = transducer_pressure
        if setpoint is not None:
            self._last_setpoint = setpoint
        if alicat_pressure is not None:
            self._last_alicat = alicat_pressure

        self._transducer_pressures.append(
            self._last_transducer if self._last_transducer is not None else 0.0
        )
        self._setpoints.append(
            self._last_setpoint if self._last_setpoint is not None else 0.0
        )
        self._alicat_pressures.append(
            self._last_alicat if self._last_alicat is not None else 0.0
        )
        
        # Throttle plot updates to reduce CPU usage
        if not self._update_pending:
            self._update_pending = True
            self._update_timer.start(self._update_interval_ms)
        
    def _do_update_plot(self) -> None:
        """Execute pending plot update (called by timer)."""
        self._update_pending = False
        self._update_plot()

    def _update_plot(self) -> None:
        """Update the plot with current buffer data using numpy for efficiency."""
        if len(self._times) == 0:
            return

        # Convert deques to numpy arrays once - more efficient than list conversion
        times = np.array(self._times, dtype=np.float64)
        transducer_data = np.array(self._transducer_pressures, dtype=np.float64)
        setpoint_data = np.array(self._setpoints, dtype=np.float64)
        alicat_data = np.array(self._alicat_pressures, dtype=np.float64)

        # Update plot lines
        self._transducer_line.setData(times, transducer_data)
        self._setpoint_line.setData(times, setpoint_data)
        self._alicat_line.setData(times, alicat_data)
        
    def clear(self) -> None:
        """Clear all data from the chart."""
        self._times.clear()
        self._transducer_pressures.clear()
        self._setpoints.clear()
        self._alicat_pressures.clear()
        self._start_time = None
        self._last_transducer = None
        self._last_setpoint = None
        self._last_alicat = None
        self._transducer_line.setData([], [])
        self._setpoint_line.setData([], [])
        self._alicat_line.setData([], [])
        # Reset update throttling
        self._update_pending = False
        self._update_timer.stop()
        
    def set_y_range(self, min_pressure: float, max_pressure: float) -> None:
        """
        Set the Y-axis range.
        
        Args:
            min_pressure: Minimum pressure in PSI
            max_pressure: Maximum pressure in PSI
        """
        return

    def set_units_label(self, units_label: str) -> None:
        """Update the Y-axis unit label."""
        label = units_label or "PSI"
        self._plot_widget.setLabel('left', 'Pressure', units=label)

    def set_manual_setpoint(self, value: float) -> None:
        self._last_setpoint = value

    def set_transducer_visible(self, visible: bool) -> None:
        self._transducer_line.setVisible(visible)

    def set_setpoint_visible(self, visible: bool) -> None:
        self._setpoint_line.setVisible(visible)

    def set_alicat_visible(self, visible: bool) -> None:
        self._alicat_line.setVisible(visible)
