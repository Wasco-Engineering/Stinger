"""
Pressure bar visualization widget.

Provides a vertical 1D pressure visualization with bands and markers.
"""

from typing import Optional, Tuple

from PyQt6.QtCore import Qt, QPoint, QPointF
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen, QFont, QPolygonF, QPainterPath
from PyQt6.QtWidgets import QWidget


class PressureBarWidget(QWidget):
    """Vertical pressure bar with acceptance bands and markers."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._pressure = 0.0
        self._min_psi = 0.0
        self._max_psi = 30.0
        self._atmosphere_psi = 0.0
        self._activation_band: Optional[Tuple[float, float]] = None
        self._deactivation_band: Optional[Tuple[float, float]] = None
        self._measured_activation: Optional[float] = None
        self._measured_deactivation: Optional[float] = None
        self._estimated_activation: Optional[float] = None
        self._estimated_deactivation: Optional[float] = None
        self._estimated_sample_count: int = 0
        self._units_label = "PSI"
        self._activation_label = 'ACT'
        self._deactivation_label = 'DEACT'
        self._axis_side = 'right'

        self._show_atmosphere_reference = True
        self._show_acceptance_bands = True
        self._show_measured_points = True

        self.setMinimumHeight(200)
    
    def set_units_label(self, units: str) -> None:
        """Set the units label for the Y-axis."""
        self._units_label = units
        self.update()

    def set_axis_side(self, side: str) -> None:
        """Set axis side: 'left' or 'right'."""
        side_normalized = str(side or '').strip().lower()
        if side_normalized not in {'left', 'right'}:
            return
        self._axis_side = side_normalized
        self.update()

    def set_scale(self, min_psi: float, max_psi: float) -> None:
        """Set the visible pressure scale range."""
        if max_psi <= min_psi:
            return
        self._min_psi = min_psi
        self._max_psi = max_psi
        self.update()

    def set_bands(
        self,
        activation_band: Optional[Tuple[float, float]],
        deactivation_band: Optional[Tuple[float, float]],
    ) -> None:
        """Set activation/deactivation acceptance bands."""
        self._activation_band = activation_band
        self._deactivation_band = deactivation_band
        self.update()

    def set_point_labels(
        self,
        activation_label: Optional[str],
        deactivation_label: Optional[str],
    ) -> None:
        """Set compact labels for activation/deactivation bands and markers."""
        if activation_label:
            self._activation_label = str(activation_label)
        if deactivation_label:
            self._deactivation_label = str(deactivation_label)
        self.update()

    def set_atmosphere_pressure(self, pressure_psi: float) -> None:
        """Set atmosphere reference pressure for the barometer line."""
        self._atmosphere_psi = pressure_psi
        self.update()

    def set_measured_points(
        self,
        activation: Optional[float],
        deactivation: Optional[float],
    ) -> None:
        """Set measured activation/deactivation points."""
        self._measured_activation = activation
        self._measured_deactivation = deactivation
        self.update()

    def set_estimated_points(
        self,
        activation: Optional[float],
        deactivation: Optional[float],
        sample_count: int,
    ) -> None:
        """Set estimated activation/deactivation points from cycling edges."""
        self._estimated_activation = activation
        self._estimated_deactivation = deactivation
        self._estimated_sample_count = max(0, int(sample_count))
        self.update()

    def set_pressure(self, pressure: float) -> None:
        """Update current pressure marker."""
        self._pressure = pressure
        self.update()

    def set_display_flags(
        self,
        show_atmosphere_reference: bool,
        show_acceptance_bands: bool,
        show_measured_points: bool,
    ) -> None:
        """Set visibility flags for bar elements."""
        self._show_atmosphere_reference = show_atmosphere_reference
        self._show_acceptance_bands = show_acceptance_bands
        self._show_measured_points = show_measured_points
        self.update()

    def _pressure_to_y(self, pressure: float, top: int, bottom: int) -> int:
        if self._max_psi == self._min_psi:
            return bottom
        ratio = (pressure - self._min_psi) / (self._max_psi - self._min_psi)
        ratio = max(0.0, min(1.0, ratio))
        return int(bottom - ratio * (bottom - top))

    def paintEvent(self, a0) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Increased axis margin for better label visibility
        axis_margin = 60
        top_margin = 26  # Extra room so units label stays above top tick labels
        if self._axis_side == 'left':
            rect = self.rect().adjusted(8 + axis_margin, top_margin, -8, -8)
        else:
            rect = self.rect().adjusted(8, top_margin, -8 - axis_margin, -8)
        top = rect.top()
        bottom = rect.bottom()
        
        # Flat light background
        painter.fillRect(rect, QColor(245, 246, 248))
        
        # Subtle border
        painter.setPen(QPen(QColor(0, 0, 0, 25), 1))
        painter.drawRect(rect)
        
        # Draw fill level tint below current pressure (subtle)
        if self._pressure > self._min_psi:
            y_marker = self._pressure_to_y(self._pressure, top, bottom)
            fill_rect = rect.adjusted(1, 0, -1, 0)
            fill_rect.setTop(y_marker)
            fill_rect.setBottom(bottom - 1)
            painter.fillRect(fill_rect, QColor(37, 99, 235, 18))  # Very subtle blue tint

        # Draw acceptance bands (clean flat style, no gradient).
        # Deactivation is drawn FIRST so that activation paints on top
        # when bands are adjacent or very narrow.
        if self._show_acceptance_bands:
            if self._deactivation_band:
                self._draw_flat_band(
                    painter, rect, self._deactivation_band, QColor(220, 38, 38),
                    self._deactivation_label,
                )
            if self._activation_band:
                self._draw_flat_band(
                    painter, rect, self._activation_band, QColor(37, 99, 235),
                    self._activation_label,
                )

        # Atmosphere reference with improved visibility
        if self._show_atmosphere_reference:
            if self._min_psi <= self._atmosphere_psi <= self._max_psi:
                y_atm = self._pressure_to_y(self._atmosphere_psi, top, bottom)
                # More visible dashed line
                painter.setPen(QPen(QColor(107, 114, 128, 180), 1.5, Qt.PenStyle.DashLine))
                painter.drawLine(rect.left(), y_atm, rect.right(), y_atm)
                painter.setFont(QFont('Arial', 8, QFont.Weight.Bold))
                # Darker label for better contrast
                painter.setPen(QPen(QColor(55, 65, 81), 1))
                painter.drawText(rect.left() + 6, y_atm - 4, 'ATM')

        # Draw axis with major and minor ticks
        self._draw_axis(painter, rect)
        
        # Draw measured points with diamond markers
        if self._show_measured_points:
            self._draw_estimated_points(painter, rect)
            if self._measured_activation is not None:
                self._draw_measured_point(
                    painter, rect, self._measured_activation,
                    QColor(37, 99, 235), self._activation_label,
                    marker_shape='diamond', side='left',
                )
            if self._measured_deactivation is not None:
                self._draw_measured_point(
                    painter, rect, self._measured_deactivation,
                    QColor(255, 140, 0), self._deactivation_label,
                    marker_shape='circle', side='right',
                )

        # Current pressure marker as pointer with glow
        self._draw_pressure_pointer(painter, rect, self._pressure)

    def _draw_band(
        self, painter: QPainter, rect, band: Tuple[float, float], color: QColor
    ) -> None:
        low, high = band
        y_low = self._pressure_to_y(low, rect.top(), rect.bottom())
        y_high = self._pressure_to_y(high, rect.top(), rect.bottom())
        band_top = min(y_low, y_high)
        band_bottom = max(y_low, y_high)
        painter.fillRect(
            rect.left() + 1,
            band_top,
            rect.width() - 1,
            max(1, band_bottom - band_top),
            color,
        )
    
    _MIN_BAND_HEIGHT_PX = 4

    def _draw_flat_band(
        self, painter: QPainter, rect, band: Tuple[float, float], color: QColor,
        label: str = '',
    ) -> None:
        """Draw an acceptance band as a clean flat rectangle with border.

        A minimum visual height of ``_MIN_BAND_HEIGHT_PX`` is enforced so
        that very narrow bands (e.g. 0.05 PSI wide) are still visible.
        An optional *label* (e.g. "ACT" / "DEACT") is rendered inside
        the band when it is tall enough.
        """
        low, high = band
        y_low = self._pressure_to_y(low, rect.top(), rect.bottom())
        y_high = self._pressure_to_y(high, rect.top(), rect.bottom())
        band_top = min(y_low, y_high)
        band_bottom = max(y_low, y_high)

        # Enforce minimum visual height (expand symmetrically around centre)
        natural_height = band_bottom - band_top
        if natural_height < self._MIN_BAND_HEIGHT_PX:
            mid = (band_top + band_bottom) / 2.0
            half = self._MIN_BAND_HEIGHT_PX / 2.0
            band_top = int(mid - half)
            band_bottom = int(mid + half)

        band_height = max(1, band_bottom - band_top)

        # Semi-transparent fill
        fill_color = QColor(color)
        fill_color.setAlpha(50)
        painter.fillRect(
            rect.left() + 2, band_top,
            rect.width() - 3, band_height,
            fill_color,
        )

        # Crisp border
        border_color = QColor(color)
        border_color.setAlpha(140)
        painter.setPen(QPen(border_color, 1))
        painter.drawRect(
            rect.left() + 2, band_top,
            rect.width() - 3, band_height,
        )

        # Draw label inside band when tall enough (at least 14 px)
        if label and band_height >= 14:
            painter.setFont(QFont('Arial', 7, QFont.Weight.Bold))
            label_color = QColor(color)
            label_color.setAlpha(200)
            painter.setPen(QPen(label_color, 1))
            text_y = band_top + band_height // 2 + 4
            painter.drawText(rect.left() + 5, text_y, label)
    
    def _draw_axis(self, painter: QPainter, rect) -> None:
        """Draw Y-axis with major and minor ticks."""
        top = rect.top()
        bottom = rect.bottom()
        
        # Draw units label at top - more prominent
        painter.setPen(QPen(QColor(55, 65, 81), 1))
        font = QFont("Arial", 12, QFont.Weight.Bold)
        painter.setFont(font)
        # Keep units above major tick labels to avoid overlap.
        font_metrics = painter.fontMetrics()
        units_y = top - 6
        if units_y < font_metrics.ascent():
            units_y = font_metrics.ascent()
        text_x = rect.right() + 12 if self._axis_side == 'right' else rect.left() - 12 - font_metrics.horizontalAdvance(self._units_label)
        painter.drawText(text_x, units_y, self._units_label)

        # Collect band limit values so we can draw labeled ticks for them
        # and suppress overlapping major-tick labels.
        band_limits = self._collect_band_limits()
        
        # Major ticks
        major_ticks = 10
        painter.setFont(QFont("Arial", 10))
        
        for i in range(major_ticks + 1):
            ratio = i / major_ticks
            y = int(bottom - ratio * (bottom - top))
            psi = self._min_psi + ratio * (self._max_psi - self._min_psi)
            
            # Major tick
            painter.setPen(QPen(QColor(156, 163, 175), 2))
            if self._axis_side == 'right':
                painter.drawLine(rect.right(), y, rect.right() + 8, y)
            else:
                painter.drawLine(rect.left(), y, rect.left() - 8, y)
            
            # Skip label if a band-limit label is nearby (avoid overlap)
            if self._near_any_band_limit(psi, band_limits):
                continue

            # Label (right-aligned) - darker for better contrast
            painter.setPen(QPen(QColor(55, 65, 81), 1))
            text = f"{psi:.1f}" if (self._max_psi - self._min_psi) < 10 else f"{psi:.0f}"
            text_rect = painter.fontMetrics().boundingRect(text)
            if self._axis_side == 'right':
                label_x = rect.right() + 12
            else:
                label_x = rect.left() - 12 - text_rect.width()
            painter.drawText(label_x, y + text_rect.height() // 3, text)
        
        # Minor ticks
        minor_ticks = major_ticks * 5
        for i in range(minor_ticks + 1):
            if i % 5 == 0:  # Skip major tick positions
                continue
            ratio = i / minor_ticks
            y = int(bottom - ratio * (bottom - top))
            
            painter.setPen(QPen(QColor(180, 180, 180), 1))
            if self._axis_side == 'right':
                painter.drawLine(rect.right(), y, rect.right() + 4, y)
            else:
                painter.drawLine(rect.left(), y, rect.left() - 4, y)

        # Band-limit ticks: prominent labeled ticks at each band boundary
        if band_limits and self._show_acceptance_bands:
            self._draw_band_limit_ticks(painter, rect, band_limits)

    def _collect_band_limits(self) -> list[float]:
        """Return deduplicated, sorted band boundary values within the visible scale."""
        seen: set[float] = set()
        for band in (self._activation_band, self._deactivation_band):
            if not band:
                continue
            for v in band:
                if self._min_psi <= v <= self._max_psi:
                    seen.add(round(v, 4))
        return sorted(seen)

    def _near_any_band_limit(self, psi: float, band_limits: list[float]) -> bool:
        """Return True if *psi* is close enough to a band limit to cause label overlap."""
        if not band_limits:
            return False
        scale_span = self._max_psi - self._min_psi
        if scale_span <= 0:
            return False
        threshold = scale_span * 0.04
        return any(abs(psi - bl) < threshold for bl in band_limits)

    def _draw_band_limit_ticks(
        self, painter: QPainter, rect, band_limits: list[float],
    ) -> None:
        """Draw labeled ticks at band boundary values."""
        top = rect.top()
        bottom = rect.bottom()
        font = QFont("Arial", 9, QFont.Weight.Bold)
        painter.setFont(font)
        fm = painter.fontMetrics()

        for val in band_limits:
            y = self._pressure_to_y(val, top, bottom)

            # Tick mark
            painter.setPen(QPen(QColor(107, 114, 128), 2))
            if self._axis_side == 'right':
                painter.drawLine(rect.right(), y, rect.right() + 10, y)
            else:
                painter.drawLine(rect.left(), y, rect.left() - 10, y)

            # Label
            scale_span = self._max_psi - self._min_psi
            text = f"{val:.1f}" if scale_span < 10 else f"{val:.0f}"
            tw = fm.horizontalAdvance(text)
            th = fm.height()
            if self._axis_side == 'right':
                label_x = rect.right() + 12
            else:
                label_x = rect.left() - 12 - tw
            label_y = y + fm.ascent() // 2

            # White background for readability
            bg_x = label_x - 2
            bg_y = label_y - fm.ascent() - 1
            painter.fillRect(int(bg_x), int(bg_y), tw + 4, th + 2, QColor(255, 255, 255, 220))

            painter.setPen(QPen(QColor(55, 65, 81), 1))
            painter.drawText(label_x, label_y, text)
    
    def _draw_measured_point(
        self, painter: QPainter, rect, pressure: float, color: QColor, label: str,
        marker_shape: str = 'diamond', side: str = 'left'
    ) -> None:
        """Draw a measured point with marker and label.

        Args:
            marker_shape: 'diamond' for activation, 'circle' for deactivation (colorblind-friendly)
            side: 'left' pins the marker/label to the left edge; 'right' mirrors them
                  to the right edge so that two nearby readings don't overlap.
        """
        top = rect.top()
        bottom = rect.bottom()
        y = self._pressure_to_y(pressure, top, bottom)

        # Full-width horizontal reference line
        line_color = QColor(color)
        line_color.setAlpha(200)
        painter.setPen(QPen(line_color, 1.5))
        painter.drawLine(rect.left() + 4, y, rect.right() - 4, y)

        marker_size = 6
        painter.setBrush(color)
        painter.setPen(QPen(QColor(255, 255, 255), 1))

        if side == 'right':
            # Mirror marker to the right edge
            marker_cx = rect.right() + 2 - marker_size  # horizontal centre of marker
            if marker_shape == 'circle':
                painter.drawEllipse(
                    int(marker_cx - marker_size),
                    int(y - marker_size),
                    marker_size * 2,
                    marker_size * 2,
                )
            else:
                diamond = QPolygonF([
                    QPointF(rect.right() + 2, y),
                    QPointF(marker_cx, y - marker_size),
                    QPointF(rect.right() + 2 - marker_size * 2, y),
                    QPointF(marker_cx, y + marker_size),
                ])
                painter.drawPolygon(diamond)

            # Label right-aligned inside the bar
            painter.setFont(QFont('Arial', 8, QFont.Weight.Bold))
            painter.setPen(QPen(QColor(26, 26, 46), 1))
            value_text = f'{label} {pressure:.2f}' if label else f'{pressure:.2f}'
            text_width = painter.fontMetrics().horizontalAdvance(value_text)
            label_x = rect.right() - text_width - 8
            label_y = y - 12 if y > rect.top() + 20 else y + 20

            bg_rect = painter.fontMetrics().boundingRect(value_text)
            bg_rect.moveTopLeft(QPoint(int(label_x - 2), int(label_y - bg_rect.height() + 2)))
            bg_rect.adjust(-2, -1, 2, 1)
            painter.fillRect(bg_rect, QColor(255, 255, 255, 220))
            painter.drawText(label_x, label_y, value_text)

        else:
            # Original left-edge marker
            marker_cx = rect.left() - 2 + marker_size
            if marker_shape == 'circle':
                painter.drawEllipse(
                    int(marker_cx - marker_size),
                    int(y - marker_size),
                    marker_size * 2,
                    marker_size * 2,
                )
            else:
                diamond = QPolygonF([
                    QPointF(rect.left() - 2, y),
                    QPointF(marker_cx, y - marker_size),
                    QPointF(rect.left() - 2 + marker_size * 2, y),
                    QPointF(marker_cx, y + marker_size),
                ])
                painter.drawPolygon(diamond)

            # Label left-aligned inside the bar
            painter.setFont(QFont('Arial', 8, QFont.Weight.Bold))
            painter.setPen(QPen(QColor(26, 26, 46), 1))
            value_text = f'{label} {pressure:.2f}' if label else f'{pressure:.2f}'
            label_x = rect.left() + 8
            label_y = y - 12 if y > rect.top() + 20 else y + 20

            bg_rect = painter.fontMetrics().boundingRect(value_text)
            bg_rect.moveTopLeft(QPoint(int(label_x - 2), int(label_y - bg_rect.height() + 2)))
            bg_rect.adjust(-2, -1, 2, 1)
            painter.fillRect(bg_rect, QColor(255, 255, 255, 220))
            painter.drawText(label_x, label_y, value_text)

    def _draw_estimated_points(self, painter: QPainter, rect) -> None:
        """Draw semi-transparent estimated lines from cycling samples."""
        if self._estimated_sample_count <= 0:
            return
        alpha = min(210, 40 + self._estimated_sample_count * 45)

        if self._estimated_activation is not None:
            self._draw_estimated_line(
                painter,
                rect,
                self._estimated_activation,
                QColor(37, 99, 235, alpha),
                side='left',
            )
        if self._estimated_deactivation is not None:
            self._draw_estimated_line(
                painter,
                rect,
                self._estimated_deactivation,
                QColor(220, 38, 38, alpha),
                side='right',
            )

    def _draw_estimated_line(
        self, painter: QPainter, rect, pressure: float, color: QColor, side: str = 'left'
    ) -> None:
        """Draw a dashed estimated line spanning the half of the bar that matches *side*.

        Activation (left) and deactivation (right) are staggered horizontally so
        their dashed lines don't completely overlap when values are close together.
        """
        y = self._pressure_to_y(pressure, rect.top(), rect.bottom())
        mid = rect.left() + rect.width() // 2
        pen = QPen(color, 1.5, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        if side == 'right':
            painter.drawLine(mid, y, rect.right() - 4, y)
        else:
            painter.drawLine(rect.left() + 4, y, mid, y)
    
    def _draw_pressure_pointer(self, painter: QPainter, rect, pressure: float) -> None:
        """Draw current pressure marker as a thin, precise line with arrow."""
        top = rect.top()
        bottom = rect.bottom()
        y = self._pressure_to_y(pressure, top, bottom)
        
        # Draw main pointer line - thinner (2px) with brighter, more contrasting color
        # Using a bright blue for better visibility on light background
        painter.setPen(QPen(QColor(37, 99, 235), 2))
        painter.drawLine(rect.left(), y, rect.right(), y)
        
        # Draw smaller arrow/triangle on right edge, proportional to thinner line
        arrow_size = 6
        arrow = QPolygonF([
            QPointF(rect.right(), y),
            QPointF(rect.right() + arrow_size * 1.2, y - arrow_size),
            QPointF(rect.right() + arrow_size * 1.2, y + arrow_size),
        ])
        painter.setBrush(QColor(37, 99, 235))
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.drawPolygon(arrow)
