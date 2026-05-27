#!/usr/bin/env python3
"""Generate cropped NO/NC indicator images from the current PyQt UI widgets."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QWidget

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ui.widgets import LEDIndicator


OUTPUT_DIR = ROOT / 'docs' / 'generated' / 'no_nc_indicators'


def get_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough,
        )
        app = QApplication([])
        app.setApplicationName('Stinger NO/NC Indicator Image Generator')
    return app


def build_indicator_widget(*, no_active: bool, nc_active: bool) -> QWidget:
    widget = QWidget()
    widget.setStyleSheet('background-color: #f8fafc;')

    layout = QHBoxLayout(widget)
    layout.setContentsMargins(18, 14, 18, 14)
    layout.setSpacing(22)

    for label, active, is_nc in (
        ('NO', no_active, False),
        ('NC', nc_active, True),
    ):
        text = QLabel(label)
        text.setFont(QFont('Segoe UI', 18, QFont.Weight.Bold))
        text.setStyleSheet('color: #4b5563; background: transparent;')
        text.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        led = LEDIndicator(size=58)
        led.set_nc_mode(is_nc)
        led.set_active(active)

        layout.addWidget(text)
        layout.addWidget(led)

    widget.adjustSize()
    return widget


def render_widget(widget: QWidget) -> QImage:
    widget.show()
    widget.raise_()
    app = get_app()
    for _ in range(6):
        app.processEvents()

    pixmap = widget.grab()
    image = QImage(pixmap.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor('#f8fafc'))

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return image


def save_indicator_image(filename: str, *, no_active: bool, nc_active: bool) -> Path:
    widget = build_indicator_widget(no_active=no_active, nc_active=nc_active)
    image = render_widget(widget)
    widget.close()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename
    if not image.save(str(path)):
        raise RuntimeError(f'Failed to save image: {path}')
    return path


def main() -> int:
    app = get_app()
    outputs = [
        save_indicator_image('no_nc_red_on.png', no_active=False, nc_active=True),
        save_indicator_image('no_nc_green_on.png', no_active=True, nc_active=False),
    ]

    print('Created NO/NC indicator images:')
    for output in outputs:
        print(f' - {output}')
    app.processEvents()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
