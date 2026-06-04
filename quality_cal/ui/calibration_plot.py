"""Calibration sweep charts: sensor vs Mensor and corrected residuals."""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from app.services.pressure_calibration import apply_error_model
from quality_cal.session import CalibrationPointResult


class CalibrationPlotWidget(QWidget):
    """Two stacked plots: measured pressures vs Mensor, then correction residuals."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._pressure_plot = pg.PlotWidget(title='Sensors vs Mensor')
        self._pressure_plot.setBackground('#ffffff')
        self._pressure_plot.showGrid(x=True, y=True, alpha=0.25)
        self._pressure_plot.setLabel('left', 'Pressure', units='psia')
        self._pressure_plot.setLabel('bottom', 'Point')
        self._mensor_curve = self._pressure_plot.plot(
            pen=pg.mkPen('#0f172a', width=2),
            symbol='o',
            symbolSize=6,
            name='Mensor',
        )
        self._alicat_curve = self._pressure_plot.plot(
            pen=pg.mkPen('#2563eb', width=2),
            symbol='s',
            symbolSize=5,
            name='Alicat',
        )
        self._transducer_curve = self._pressure_plot.plot(
            pen=pg.mkPen('#b45309', width=2),
            symbol='t',
            symbolSize=5,
            name='Transducer',
        )
        self._alicat_corr_curve = self._pressure_plot.plot(
            pen=pg.mkPen('#2563eb', width=1, style=pg.QtCore.Qt.PenStyle.DashLine),
            symbol='x',
            symbolSize=5,
            name='Alicat (corr)',
        )
        self._transducer_corr_curve = self._pressure_plot.plot(
            pen=pg.mkPen('#b45309', width=1, style=pg.QtCore.Qt.PenStyle.DashLine),
            symbol='+',
            symbolSize=5,
            name='Transducer (corr)',
        )
        self._pressure_plot.addLegend(offset=(4, 4))
        layout.addWidget(self._pressure_plot, 1)

        self._residual_plot = pg.PlotWidget(title='Δ vs Mensor (psia)')
        self._residual_plot.setBackground('#ffffff')
        self._residual_plot.showGrid(x=True, y=True, alpha=0.25)
        self._residual_plot.setLabel('left', 'Deviation', units='psia')
        self._residual_plot.setLabel('bottom', 'Point')
        self._residual_plot.addLine(y=0, pen=pg.mkPen('#94a3b8', width=1))
        self._alicat_raw_res = self._residual_plot.plot(
            pen=None,
            symbol='s',
            symbolBrush='#2563eb',
            symbolSize=6,
            name='Alicat raw',
        )
        self._alicat_corr_res = self._residual_plot.plot(
            pen=None,
            symbol='x',
            symbolBrush='#1d4ed8',
            symbolSize=7,
            name='Alicat corr',
        )
        self._transducer_raw_res = self._residual_plot.plot(
            pen=None,
            symbol='t',
            symbolBrush='#b45309',
            symbolSize=6,
            name='Xducer raw',
        )
        self._transducer_corr_res = self._residual_plot.plot(
            pen=None,
            symbol='+',
            symbolBrush='#92400e',
            symbolSize=8,
            name='Xducer corr',
        )
        self._residual_plot.addLegend(offset=(4, 4))
        layout.addWidget(self._residual_plot, 1)

    def update_points(
        self,
        points: list[CalibrationPointResult],
        *,
        alicat_model: Optional[dict[str, Any]] = None,
        transducer_model: Optional[dict[str, Any]] = None,
    ) -> None:
        used = [p for p in points if p.mensor_used and p.mensor_psia is not None]
        if not used:
            self.clear()
            return

        xs = np.array([float(p.point_index) for p in used], dtype=float)
        mensor = np.array([float(p.mensor_psia) for p in used], dtype=float)

        def _sensor_values(
            attr: str,
            model: Optional[dict[str, Any]],
        ) -> tuple[np.ndarray, np.ndarray]:
            raw: list[float] = []
            corrected: list[float] = []
            for point in used:
                value = getattr(point, attr)
                if value is None:
                    raw.append(np.nan)
                    corrected.append(np.nan)
                    continue
                raw.append(float(value))
                if model is not None:
                    corrected.append(float(apply_error_model(value, model)))
                else:
                    corrected.append(np.nan)
            return np.array(raw, dtype=float), np.array(corrected, dtype=float)

        alicat_raw, alicat_corr = _sensor_values('alicat_psia', alicat_model)
        xducer_raw, xducer_corr = _sensor_values('transducer_psia', transducer_model)

        self._mensor_curve.setData(xs, mensor)
        self._alicat_curve.setData(xs, alicat_raw)
        self._transducer_curve.setData(xs, xducer_raw)
        self._alicat_corr_curve.setData(xs, alicat_corr)
        self._transducer_corr_curve.setData(xs, xducer_corr)

        self._alicat_raw_res.setData(xs, mensor - alicat_raw)
        self._transducer_raw_res.setData(xs, mensor - xducer_raw)
        self._alicat_corr_res.setData(xs, mensor - alicat_corr)
        self._transducer_corr_res.setData(xs, mensor - xducer_corr)

    def clear(self) -> None:
        empty = np.array([], dtype=float)
        for curve in (
            self._mensor_curve,
            self._alicat_curve,
            self._transducer_curve,
            self._alicat_corr_curve,
            self._transducer_corr_curve,
            self._alicat_raw_res,
            self._alicat_corr_res,
            self._transducer_raw_res,
            self._transducer_corr_res,
        ):
            curve.setData(empty, empty)
