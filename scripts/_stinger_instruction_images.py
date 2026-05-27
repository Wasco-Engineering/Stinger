#!/usr/bin/env python3
"""Shared helpers for deterministic Stinger work-instruction image generation."""

from __future__ import annotations

import argparse
import io
import math
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ui.login_dialog import LoginDialog
from app.ui.main_window import MainWindow
from app.ui.port_column import PortColumn
from app.services.ptp_service import derive_test_setup, load_ptp_from_dump


@dataclass(frozen=True)
class SampleWorkOrder:
    operator_id: str
    shop_order: str
    part_id: str
    sequence_id: str
    order_qty: str
    process: str
    ptp_setpoint: float
    ptp_direction: str
    ptp_units: str
    ptp_lookup_part_id: str


@dataclass(frozen=True)
class CalloutSpec:
    key: str
    text: str
    anchor: str = 'top'


@dataclass(frozen=True)
class SceneSpec:
    id: str
    step_number: int
    slug: str
    title: str
    caption: str
    callouts: tuple[CalloutSpec, ...]
    window_kind: str
    build_state: Callable[[QApplication, SampleWorkOrder], 'SceneBuild']
    filename_stem: str


@dataclass(frozen=True)
class SceneBuild:
    content: QPixmap
    targets: dict[str, QRect]


@dataclass(frozen=True)
class ExportRecord:
    scene: SceneSpec
    variant: str
    path: Path


class RenderVariant(str, Enum):
    ANNOTATED = 'annotated'
    CLEAN = 'clean'
    BOTH = 'both'


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Generate deterministic Stinger work-instruction screenshots.',
    )
    parser.add_argument(
        '--workflow',
        choices=('qal15', 'qal16'),
        required=True,
        help='Workflow image set to render.',
    )
    parser.add_argument(
        '--variant',
        choices=tuple(variant.value for variant in RenderVariant),
        default=RenderVariant.ANNOTATED.value,
        help='Export annotated, clean, or both variants.',
    )
    parser.add_argument(
        '--scene',
        default='all',
        help='Specific scene id to render, or "all" for the full set.',
    )
    parser.add_argument(
        '--output-dir',
        default=str(ROOT / 'docs' / 'generated'),
        help='Base output directory for generated workflow folders.',
    )
    parser.add_argument(
        '--review-sheet',
        action='store_true',
        help='Also generate a simple review-sheet contact image.',
    )
    return parser


def get_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough,
        )
        app = QApplication([])
        app.setApplicationName('Stinger Instruction Image Generator')
    return app


def process_events(app: QApplication, cycles: int = 6) -> None:
    for _ in range(cycles):
        app.processEvents()


def grab_widget(widget: QWidget, app: QApplication) -> QPixmap:
    widget.show()
    widget.raise_()
    process_events(app)
    return widget.grab()


def save_image(image: QImage, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(output_path)):
        raise RuntimeError(f'Failed to save image: {output_path}')


def sample_work_order(workflow: str) -> SampleWorkOrder:
    workflow_key = workflow.strip().lower()
    if workflow_key == 'qal15':
        return SampleWorkOrder(
            operator_id='NB-01',
            shop_order='51074234',
            part_id='SPS01496-02',
            sequence_id='300',
            order_qty='24',
            process='QAL15',
            ptp_setpoint=10.0,
            ptp_direction='Increasing',
            ptp_units='PSI',
            ptp_lookup_part_id='SPS01414-03',
        )
    if workflow_key == 'qal16':
        return SampleWorkOrder(
            operator_id='NB-01',
            shop_order='51074234',
            part_id='17025',
            sequence_id='399',
            order_qty='24',
            process='QAL16',
            ptp_setpoint=400.0,
            ptp_direction='Decreasing',
            ptp_units='Torr',
            ptp_lookup_part_id='17025',
        )
    raise ValueError(f'Unsupported workflow: {workflow}')


def workflow_scene_catalog(workflow: str) -> list[SceneSpec]:
    workflow_key = workflow.strip().lower()
    if workflow_key == 'qal15':
        return _qal15_scenes()
    if workflow_key == 'qal16':
        return _qal16_scenes()
    raise ValueError(f'Unsupported workflow: {workflow}')


def select_scenes(workflow: str, scene_id: str) -> list[SceneSpec]:
    catalog = workflow_scene_catalog(workflow)
    requested = scene_id.strip().lower()
    if requested == 'all':
        return catalog
    for scene in catalog:
        if scene.id.lower() == requested:
            return [scene]
    raise ValueError(f'Unknown scene "{scene_id}" for workflow "{workflow}"')


def render_variant_image(scene: SceneSpec, build: SceneBuild, variant: str) -> QImage:
    content = build.content.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    canvas = QImage(content)
    if variant == RenderVariant.CLEAN.value:
        return canvas

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    _draw_step_banner(painter, canvas.size(), scene)
    for index, callout in enumerate(scene.callouts, start=1):
        target = build.targets.get(callout.key)
        if target is None:
            raise RuntimeError(f'Missing callout target "{callout.key}" for scene "{scene.id}"')
        _draw_callout(painter, canvas.size(), target, callout, index)

    painter.end()
    return canvas


def generate_instruction_images(
    *,
    workflow: str,
    output_dir: Path,
    variant: str,
    scene_id: str = 'all',
    review_sheet: bool = False,
) -> tuple[list[ExportRecord], Path | None]:
    app = get_app()
    sample = sample_work_order(workflow)
    scenes = select_scenes(workflow, scene_id)
    variants = _variants_to_render(variant)
    workflow_root = output_dir.resolve() / workflow.strip().lower()
    exports: list[ExportRecord] = []
    review_records: list[tuple[SceneSpec, Path]] = []

    for scene in scenes:
        build = scene.build_state(app, sample)
        for current_variant in variants:
            image = render_variant_image(scene, build, current_variant)
            destination = workflow_root / current_variant / f'{scene.filename_stem}.png'
            save_image(image, destination)
            exports.append(ExportRecord(scene=scene, variant=current_variant, path=destination))
            if current_variant == RenderVariant.ANNOTATED.value:
                review_records.append((scene, destination))

    review_path: Path | None = None
    if review_sheet:
        if not review_records:
            review_records = [(record.scene, record.path) for record in exports]
        review_path = workflow_root / f'{workflow.strip().lower()}_review_sheet.png'
        save_image(_build_review_sheet(review_records), review_path)

    process_events(app)
    return exports, review_path


def generate_legacy_qal15_setup_images(output_dir: Path) -> list[Path]:
    app = get_app()
    sample = sample_work_order('qal15')
    created: list[Path] = []
    for scene in workflow_scene_catalog('qal15'):
        build = scene.build_state(app, sample)
        image = render_variant_image(scene, build, RenderVariant.ANNOTATED.value)
        destination = output_dir.resolve() / f'{scene.filename_stem}.png'
        save_image(image, destination)
        created.append(destination)
    process_events(app)
    return created


def _variants_to_render(variant: str) -> list[str]:
    normalized = variant.strip().lower()
    if normalized == RenderVariant.BOTH.value:
        return [RenderVariant.ANNOTATED.value, RenderVariant.CLEAN.value]
    if normalized in {RenderVariant.ANNOTATED.value, RenderVariant.CLEAN.value}:
        return [normalized]
    raise ValueError(f'Unsupported variant: {variant}')


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _draw_step_banner(painter: QPainter, canvas_size: QSize, scene: SceneSpec) -> None:
    margin = 26
    banner_width = min(canvas_size.width() - (margin * 2), 760)
    banner_height = 112
    rect = QRect(margin, margin, banner_width, banner_height)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 230))
    painter.drawRoundedRect(QRectF(rect), 18, 18)

    accent = QRect(rect.x(), rect.y(), 12, rect.height())
    painter.setBrush(QColor('#2563eb'))
    painter.drawRoundedRect(QRectF(accent), 18, 18)
    painter.fillRect(accent.adjusted(6, 0, 0, 0), QColor('#2563eb'))

    painter.setPen(QColor('#1d4ed8'))
    painter.setFont(QFont('Segoe UI', 10, QFont.Weight.Bold))
    painter.drawText(
        QRect(rect.x() + 28, rect.y() + 12, rect.width() - 40, 18),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        f'STEP {scene.step_number:02d}',
    )

    painter.setPen(QColor('#111827'))
    painter.setFont(QFont('Segoe UI', 18, QFont.Weight.Bold))
    painter.drawText(
        QRect(rect.x() + 28, rect.y() + 28, rect.width() - 40, 34),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        scene.title,
    )

    painter.setPen(QColor('#374151'))
    painter.setFont(QFont('Segoe UI', 10))
    painter.drawText(
        QRect(rect.x() + 28, rect.y() + 66, rect.width() - 40, 34),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
        scene.caption,
    )


def _draw_callout(
    painter: QPainter,
    canvas_size: QSize,
    target: QRect,
    callout: CalloutSpec,
    index: int,
) -> None:
    margin = 24
    box_width = min(290, canvas_size.width() - (margin * 2))
    box_height = 66
    bubble_rect = _callout_rect(target, canvas_size, box_width, box_height, callout.anchor, margin)

    painter.setPen(QPen(QColor('#2563eb'), 3))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(target.adjusted(-4, -4, 4, 4)), 10, 10)

    source_point = _callout_source_point(target, callout.anchor)
    bubble_point = _callout_bubble_point(bubble_rect, callout.anchor)
    painter.setPen(QPen(QColor('#2563eb'), 2))
    painter.drawLine(source_point, bubble_point)

    shadow_rect = bubble_rect.adjusted(4, 4, 4, 4)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(15, 23, 42, 28))
    painter.drawRoundedRect(QRectF(shadow_rect), 14, 14)

    painter.setBrush(QColor(255, 255, 255, 244))
    painter.drawRoundedRect(QRectF(bubble_rect), 14, 14)

    badge_rect = QRect(bubble_rect.x() + 12, bubble_rect.y() + 16, 28, 28)
    painter.setBrush(QColor('#2563eb'))
    painter.drawEllipse(QRectF(badge_rect))
    painter.setPen(QColor('white'))
    painter.setFont(QFont('Segoe UI', 11, QFont.Weight.Bold))
    painter.drawText(badge_rect, int(Qt.AlignmentFlag.AlignCenter), str(index))

    painter.setPen(QColor('#111827'))
    painter.setFont(QFont('Segoe UI', 10))
    painter.drawText(
        QRect(bubble_rect.x() + 50, bubble_rect.y() + 12, bubble_rect.width() - 62, bubble_rect.height() - 20),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
        callout.text,
    )


def _callout_rect(
    target: QRect,
    canvas_size: QSize,
    box_width: int,
    box_height: int,
    anchor: str,
    margin: int,
) -> QRect:
    target_center = target.center()
    if anchor == 'left':
        x = max(margin, target.left() - box_width - 44)
        y = _clamp(target_center.y() - box_height // 2, margin, canvas_size.height() - margin - box_height)
    elif anchor == 'right':
        x = min(canvas_size.width() - margin - box_width, target.right() + 44)
        y = _clamp(target_center.y() - box_height // 2, margin, canvas_size.height() - margin - box_height)
    elif anchor == 'bottom':
        x = _clamp(target_center.x() - box_width // 2, margin, canvas_size.width() - margin - box_width)
        y = min(canvas_size.height() - margin - box_height, target.bottom() + 38)
    else:
        x = _clamp(target_center.x() - box_width // 2, margin, canvas_size.width() - margin - box_width)
        y = max(margin, target.top() - box_height - 38)
    return QRect(int(x), int(y), box_width, box_height)


def _callout_source_point(target: QRect, anchor: str) -> QPoint:
    if anchor == 'left':
        return QPoint(target.left(), target.center().y())
    if anchor == 'right':
        return QPoint(target.right(), target.center().y())
    if anchor == 'bottom':
        return QPoint(target.center().x(), target.bottom())
    return QPoint(target.center().x(), target.top())


def _callout_bubble_point(bubble_rect: QRect, anchor: str) -> QPoint:
    if anchor == 'left':
        return QPoint(bubble_rect.right(), bubble_rect.center().y())
    if anchor == 'right':
        return QPoint(bubble_rect.left(), bubble_rect.center().y())
    if anchor == 'bottom':
        return QPoint(bubble_rect.center().x(), bubble_rect.top())
    return QPoint(bubble_rect.center().x(), bubble_rect.bottom())


def _build_review_sheet(records: Iterable[tuple[SceneSpec, Path]]) -> QImage:
    record_list = list(records)
    if not record_list:
        raise ValueError('Cannot build a review sheet without records.')

    from PIL import Image, ImageDraw, ImageFont

    columns = 2
    card_width = 540
    card_height = 320
    outer_margin = 32
    gutter = 20
    rows = (len(record_list) + columns - 1) // columns
    width = outer_margin * 2 + columns * card_width + (columns - 1) * gutter
    height = outer_margin * 2 + rows * card_height + (rows - 1) * gutter + 60

    canvas = Image.new('RGBA', (width, height), '#f3f4f6')
    draw = ImageDraw.Draw(canvas)

    title_font = _load_review_font(22, bold=True)
    body_font = _load_review_font(11, bold=False)
    card_title_font = _load_review_font(12, bold=True)
    card_body_font = _load_review_font(10, bold=False)

    draw.text((outer_margin, 18), 'Stinger Review Sheet', fill='#111827', font=title_font)
    draw.text(
        (outer_margin, 44),
        'Verify text legibility, scene order, and operator guidance before updating the work instruction.',
        fill='#4b5563',
        font=body_font,
    )

    for index, record in enumerate(record_list):
        row = index // columns
        column = index % columns
        x = outer_margin + column * (card_width + gutter)
        y = outer_margin + 60 + row * (card_height + gutter)
        card_rect = QRect(x, y, card_width, card_height)
        draw.rounded_rectangle((x, y, x + card_width, y + card_height), radius=18, fill='white')
        draw.text((x + 18, y + 14), f'{record[0].step_number:02d}. {record[0].title}', fill='#111827', font=card_title_font)
        draw.text((x + 18, y + 38), record[0].id, fill='#4b5563', font=card_body_font)

        preview_x = x + 18
        preview_y = y + 66
        preview_width = card_width - 36
        preview_height = card_height - 84
        draw.rounded_rectangle(
            (preview_x, preview_y, preview_x + preview_width, preview_y + preview_height),
            radius=12,
            outline='#d1d5db',
            width=1,
        )

        with Image.open(record[1]) as preview_image:
            preview = preview_image.convert('RGBA')
            preview.thumbnail((preview_width, preview_height), Image.Resampling.LANCZOS)
            paste_x = preview_x + (preview_width - preview.width) // 2
            paste_y = preview_y + (preview_height - preview.height) // 2
            canvas.alpha_composite(preview, dest=(paste_x, paste_y))

    buffer = io.BytesIO()
    canvas.save(buffer, format='PNG')
    review_sheet = QImage.fromData(buffer.getvalue(), 'PNG')
    if review_sheet.isNull():
        raise RuntimeError('Failed to convert the review sheet into a QImage.')
    return review_sheet


def _load_review_font(size: int, *, bold: bool) -> object:
    from PIL import ImageFont

    font_names = ('segoeuib.ttf', 'arialbd.ttf') if bold else ('segoeui.ttf', 'arial.ttf')
    windows_font_dir = Path('C:/Windows/Fonts')
    for name in font_names:
        candidate = windows_font_dir / name
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _load_demo_ptp_profile(sample: SampleWorkOrder) -> dict[str, object]:
    raw_params = load_ptp_from_dump(sample.ptp_lookup_part_id, sample.sequence_id)
    if not raw_params:
        raw_params = {
            'ActivationTarget': str(sample.ptp_setpoint),
            'TargetActivationDirection': sample.ptp_direction,
            'UnitsOfMeasure': '1' if sample.ptp_units.upper() == 'PSI' else '21',
            'IncreasingLowerLimit': '9.5',
            'IncreasingUpperLimit': '10.5',
            'DecreasingLowerLimit': '7.5',
            'DecreasingUpperLimit': '10.0',
            'ResetBandLowerLimit': '-Inf',
            'ResetBandUpperLimit': 'Inf',
            'PressureReference': 'Gauge',
            'NormallyOpenTerminal': '1',
            'NormallyClosedTerminal': '3',
            'CommonTerminal': '4',
        }

    setup = derive_test_setup(sample.ptp_lookup_part_id, sample.sequence_id, raw_params)
    direction = str(setup.activation_direction or sample.ptp_direction or 'Increasing').strip().lower()
    activation_band_raw = setup.bands.get('decreasing' if direction == 'decreasing' else 'increasing', {})
    deactivation_band_raw = setup.bands.get('increasing' if direction == 'decreasing' else 'decreasing', {})
    target = float(setup.activation_target if setup.activation_target is not None else sample.ptp_setpoint)
    units_label = str(setup.units_label or sample.ptp_units)

    default_width = max(abs(target) * 0.1, 1.0)
    activation_band = _resolve_visual_band(activation_band_raw, target, default_width)
    deactivation_band = _resolve_visual_band(deactivation_band_raw, target, default_width)

    scale_low = min(activation_band[0], deactivation_band[0], target)
    scale_high = max(activation_band[1], deactivation_band[1], target)
    scale_padding = max((scale_high - scale_low) * 0.2, default_width * 0.5)
    precision_low = scale_low - max((scale_high - scale_low) * 0.1, default_width * 0.25)
    precision_high = scale_high + max((scale_high - scale_low) * 0.1, default_width * 0.25)

    return {
        'lookup_part_id': sample.ptp_lookup_part_id,
        'params': raw_params,
        'units_label': units_label,
        'activation_target': target,
        'direction': str(setup.activation_direction or sample.ptp_direction),
        'activation_band': activation_band,
        'deactivation_band': deactivation_band,
        'scale_min': scale_low - scale_padding,
        'scale_max': scale_high + scale_padding,
        'precision_min': precision_low,
        'precision_max': precision_high,
    }


def _resolve_visual_band(band: dict[str, float | None], target: float, fallback_width: float) -> tuple[float, float]:
    lower = band.get('lower')
    upper = band.get('upper')
    lower_finite = lower is not None and math.isfinite(lower)
    upper_finite = upper is not None and math.isfinite(upper)

    if lower_finite and upper_finite:
        low_value = float(lower)
        high_value = float(upper)
        if high_value > low_value:
            return low_value, high_value

    if lower_finite and not upper_finite:
        low_value = float(lower)
        return low_value, max(low_value + fallback_width, target)

    if upper_finite and not lower_finite:
        high_value = float(upper)
        return high_value - fallback_width, high_value

    half_width = fallback_width / 2.0
    return target - half_width, target + half_width


def _widget_rect(widget: QWidget, ancestor: QWidget) -> QRect:
    top_left = widget.mapTo(ancestor, QPoint(0, 0))
    return QRect(top_left, widget.size())


def _translated_rect(widget: QWidget, ancestor: QWidget, offset: QPoint) -> QRect:
    return _widget_rect(widget, ancestor).translated(offset)


def _styled_dialog_scene(
    app: QApplication,
    window: MainWindow,
    dialog: LoginDialog,
) -> tuple[QPixmap, QPoint]:
    window_pixmap = grab_widget(window, app)
    dialog.adjustSize()
    dialog_pixmap = grab_widget(dialog, app)

    scene = QImage(window_pixmap.size(), QImage.Format.Format_ARGB32_Premultiplied)
    scene.fill(QColor('white'))

    painter = QPainter(scene)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawPixmap(0, 0, window_pixmap)
    painter.fillRect(scene.rect(), QColor(15, 23, 42, 94))

    dialog_x = (scene.width() - dialog_pixmap.width()) // 2
    dialog_y = (scene.height() - dialog_pixmap.height()) // 2
    painter.fillRect(
        QRect(dialog_x + 10, dialog_y + 12, dialog_pixmap.width(), dialog_pixmap.height()),
        QColor(17, 24, 39, 38),
    )
    painter.drawPixmap(dialog_x, dialog_y, dialog_pixmap)
    painter.end()
    return QPixmap.fromImage(scene), QPoint(dialog_x, dialog_y)


def _create_base_window(sample: SampleWorkOrder) -> MainWindow:
    window = MainWindow(config={}, ui_bridge=None)
    window.resize(1600, 900)
    _populate_common_window(window, sample)
    return window


def _populate_common_window(window: MainWindow, sample: SampleWorkOrder) -> None:
    ptp_profile = _load_demo_ptp_profile(sample)
    window.update_work_order_display(
        {
            'operator_id': sample.operator_id,
            'shop_order': sample.shop_order,
            'part_id': sample.part_id,
            'sequence_id': sample.sequence_id,
            'process_id': sample.process,
            'completed': 3,
            'total': 24,
        },
    )
    window._on_ptp_updated(
        {
            'part_id': sample.part_id,
            'sequence_id': sample.sequence_id,
            'source': (
                'Validated work order'
                if sample.ptp_lookup_part_id == sample.part_id
                else f'Validated work order | demo PTP from {ptp_profile["lookup_part_id"]}/{sample.sequence_id}'
            ),
            'units_label': str(ptp_profile['units_label']),
            'params': ptp_profile['params'],
        },
    )
    window._status_data.update(
        {
            'system': 'Ready',
            'database': 'Connected',
            'hardware': 'Online',
            'hardware_port_a': 'Ready',
            'hardware_port_b': 'Ready',
            'last_error': 'None',
        },
    )
    window._refresh_status_level()

    primary_label = 'Test' if sample.process == 'QAL16' else 'Pressurize'
    _configure_port(
        window._port_a_widget,
        serial=1207,
        sample=sample,
        pressure=(760.0 if str(ptp_profile['units_label']).lower() == 'torr' else 0.0),
        primary={'label': primary_label, 'enabled': True, 'action': 'start_test', 'color': 'green'},
        cancel={'label': 'Vent', 'enabled': False, 'action': 'vent'},
    )
    _configure_port(
        window._port_b_widget,
        serial=1208,
        sample=sample,
        pressure=(760.0 if str(ptp_profile['units_label']).lower() == 'torr' else 0.0),
        primary={'label': primary_label, 'enabled': True, 'action': 'start_test', 'color': 'green'},
        cancel={'label': 'Vent', 'enabled': False, 'action': 'vent'},
    )


def _configure_port(
    port: PortColumn,
    *,
    serial: int,
    sample: SampleWorkOrder,
    pressure: float,
    primary: dict,
    cancel: dict,
    activation: float | None = None,
    deactivation: float | None = None,
    in_spec: bool | None = None,
    switch_state: tuple[bool, bool] = (True, True),
    viz_updates: dict | None = None,
) -> None:
    ptp_profile = _load_demo_ptp_profile(sample)
    port.set_serial(serial)
    port.set_pressure(pressure, str(ptp_profile['units_label']))
    viz_data = {
        'min_psi': float(ptp_profile['scale_min']),
        'max_psi': float(ptp_profile['scale_max']),
        'activation_band': ptp_profile['activation_band'],
        'deactivation_band': ptp_profile['deactivation_band'],
        'show_atmosphere_reference': True,
        'show_acceptance_bands': True,
        'show_measured_points': activation is not None or deactivation is not None,
    }
    if viz_updates:
        viz_data.update(viz_updates)
    port.set_pressure_visualization(viz_data)
    port.set_result(activation, deactivation, in_spec)
    port.set_switch_state(*switch_state)
    port.set_button_state(primary, cancel)


def _populate_validated_login(dialog: LoginDialog, sample: SampleWorkOrder) -> None:
    dialog.validation_timer.stop()
    for field, value in (
        (dialog.operator_id_input, sample.operator_id),
        (dialog.shop_order_input, sample.shop_order),
    ):
        previous = field.blockSignals(True)
        field.setText(value)
        field.blockSignals(previous)

    dialog.work_order_details = {
        'ShopOrder': sample.shop_order,
        'PartID': sample.part_id,
        'SequenceID': sample.sequence_id,
        'OrderQTY': sample.order_qty,
    }
    dialog._manual_entry_mode = False
    dialog._set_shop_order_validity(True)
    dialog._update_details(dialog.work_order_details)
    dialog.status_label.setText('Shop Order Validated.')
    dialog.status_label.setStyleSheet('color: #16a34a; font-weight: bold;')
    dialog._update_login_button_state()


def _assert_text(widget: QWidget, expected: str) -> None:
    value = widget.text() if hasattr(widget, 'text') else None
    if value != expected:
        raise RuntimeError(f'Expected "{expected}" but found "{value}"')


def _assert_contains(widget: QWidget, expected: str) -> None:
    value = widget.text() if hasattr(widget, 'text') else None
    if not isinstance(value, str) or expected not in value:
        raise RuntimeError(f'Expected "{expected}" within "{value}"')


def _build_qal15_login_open(app: QApplication, sample: SampleWorkOrder) -> SceneBuild:
    window = _create_base_window(sample)
    dialog = LoginDialog(window, config={})
    dialog.resize(560, dialog.height())
    pixmap, dialog_offset = _styled_dialog_scene(app, window, dialog)

    if dialog.login_button.isEnabled():
        raise RuntimeError('Login button should be disabled on the blank login scene.')

    targets = {
        'operator_id': _translated_rect(dialog.operator_id_input, dialog, dialog_offset),
        'shop_order': _translated_rect(dialog.shop_order_input, dialog, dialog_offset),
        'login_button': _translated_rect(dialog.login_button, dialog, dialog_offset),
    }
    dialog.close()
    window.close()
    return SceneBuild(content=pixmap, targets=targets)


def _build_qal15_login_validated(app: QApplication, sample: SampleWorkOrder) -> SceneBuild:
    window = _create_base_window(sample)
    dialog = LoginDialog(window, config={})
    dialog.resize(760, dialog.height())
    _populate_validated_login(dialog, sample)
    pixmap = grab_widget(dialog, app)

    if not dialog.login_button.isEnabled():
        raise RuntimeError('Validated login scene should enable the Login button.')
    _assert_contains(dialog.part_id_input, sample.part_id)
    _assert_contains(dialog.sequence_input, sample.sequence_id)

    targets = {
        'part_id': _widget_rect(dialog.part_id_input, dialog),
        'sequence': _widget_rect(dialog.sequence_input, dialog),
        'login_button': _widget_rect(dialog.login_button, dialog),
    }
    dialog.close()
    window.close()
    return SceneBuild(content=pixmap, targets=targets)


def _build_qal15_station_ready(app: QApplication, sample: SampleWorkOrder) -> SceneBuild:
    window = _create_base_window(sample)
    _assert_text(window._lbl_process, 'QAL15')
    _assert_text(window._port_a_widget._btn_primary, 'Pressurize')
    pixmap = grab_widget(window, app)
    targets = {
        'process': _widget_rect(window._lbl_process, window),
        'ptp_setpoint': _widget_rect(window._lbl_ptp_setpoint, window),
        'port_a_primary': _widget_rect(window._port_a_widget._btn_primary, window),
    }
    window.close()
    return SceneBuild(content=pixmap, targets=targets)


def _build_qal16_login_open(app: QApplication, sample: SampleWorkOrder) -> SceneBuild:
    window = _create_base_window(sample)
    dialog = LoginDialog(window, config={})
    dialog.resize(560, dialog.height())
    pixmap, dialog_offset = _styled_dialog_scene(app, window, dialog)

    if dialog.login_button.isEnabled():
        raise RuntimeError('Login button should be disabled on the blank login scene.')

    targets = {
        'operator_id': _translated_rect(dialog.operator_id_input, dialog, dialog_offset),
        'shop_order': _translated_rect(dialog.shop_order_input, dialog, dialog_offset),
        'login_button': _translated_rect(dialog.login_button, dialog, dialog_offset),
    }
    dialog.close()
    window.close()
    return SceneBuild(content=pixmap, targets=targets)


def _build_qal16_login_validated(app: QApplication, sample: SampleWorkOrder) -> SceneBuild:
    window = _create_base_window(sample)
    dialog = LoginDialog(window, config={})
    dialog.resize(760, dialog.height())
    _populate_validated_login(dialog, sample)
    pixmap = grab_widget(dialog, app)

    if not dialog.login_button.isEnabled():
        raise RuntimeError('Validated login scene should enable the Login button.')
    _assert_contains(dialog.part_id_input, sample.part_id)
    _assert_contains(dialog.sequence_input, sample.sequence_id)

    targets = {
        'part_id': _widget_rect(dialog.part_id_input, dialog),
        'sequence': _widget_rect(dialog.sequence_input, dialog),
        'login_button': _widget_rect(dialog.login_button, dialog),
    }
    dialog.close()
    window.close()
    return SceneBuild(content=pixmap, targets=targets)


def _build_qal16_ready_to_test(app: QApplication, sample: SampleWorkOrder) -> SceneBuild:
    window = _create_base_window(sample)
    _assert_text(window._lbl_process, 'QAL16')
    _assert_text(window._port_a_widget._btn_primary, 'Test')
    pixmap = grab_widget(window, app)
    targets = {
        'process': _widget_rect(window._lbl_process, window),
        'ptp_setpoint': _widget_rect(window._lbl_ptp_setpoint, window),
        'port_a_primary': _widget_rect(window._port_a_widget._btn_primary, window),
    }
    window.close()
    return SceneBuild(content=pixmap, targets=targets)


def _build_qal16_cycling(app: QApplication, sample: SampleWorkOrder) -> SceneBuild:
    window = _create_base_window(sample)
    _configure_port(
        window._port_a_widget,
        serial=1207,
        sample=sample,
        pressure=430.0,
        primary={'label': 'Cycling…', 'enabled': False, 'action': None, 'color': 'yellow', 'blink': False},
        cancel={'label': 'Cancel', 'enabled': True, 'action': 'cancel'},
        viz_updates={'show_measured_points': False},
    )
    _configure_port(
        window._port_b_widget,
        serial=1208,
        sample=sample,
        pressure=0.0,
        primary={'label': 'Test', 'enabled': True, 'action': 'start_test', 'color': 'green'},
        cancel={'label': 'Vent', 'enabled': False, 'action': 'vent'},
    )

    _assert_text(window._port_a_widget._btn_primary, 'Cycling…')
    _assert_text(window._port_a_widget._btn_cancel, 'Cancel')
    pixmap = grab_widget(window, app)
    targets = {
        'port_a_card': _widget_rect(window._port_a_widget, window),
        'port_a_primary': _widget_rect(window._port_a_widget._btn_primary, window),
        'port_a_cancel': _widget_rect(window._port_a_widget._btn_cancel, window),
    }
    window.close()
    return SceneBuild(content=pixmap, targets=targets)


def _build_qal16_precision_test(app: QApplication, sample: SampleWorkOrder) -> SceneBuild:
    window = _create_base_window(sample)
    ptp_profile = _load_demo_ptp_profile(sample)
    _configure_port(
        window._port_a_widget,
        serial=1207,
        sample=sample,
        pressure=402.0,
        primary={'label': 'Testing…', 'enabled': False, 'action': None, 'color': 'yellow', 'blink': False},
        cancel={'label': 'Cancel', 'enabled': True, 'action': 'cancel'},
        viz_updates={
            'min_psi': float(ptp_profile['precision_min']),
            'max_psi': float(ptp_profile['precision_max']),
            'estimated_activation': 402.0,
            'estimated_deactivation': 482.0,
            'estimated_sample_count': 18,
            'show_measured_points': True,
        },
    )

    _assert_text(window._port_a_widget._btn_primary, 'Testing…')
    pixmap = grab_widget(window, app)
    targets = {
        'port_a_chart': _widget_rect(window._port_a_widget._pressure_bar, window),
        'port_a_pill': _widget_rect(window._port_a_widget._pill_act_deact, window),
        'port_a_primary': _widget_rect(window._port_a_widget._btn_primary, window),
    }
    window.close()
    return SceneBuild(content=pixmap, targets=targets)


def _build_qal16_review_pass(app: QApplication, sample: SampleWorkOrder) -> SceneBuild:
    window = _create_base_window(sample)
    _configure_port(
        window._port_a_widget,
        serial=1207,
        sample=sample,
        pressure=482.0,
        primary={'label': 'Record Success', 'enabled': True, 'action': 'record_success', 'color': 'green'},
        cancel={'label': 'Retest', 'enabled': True, 'action': 'retest'},
        activation=402.0,
        deactivation=482.0,
        in_spec=True,
    )

    _assert_text(window._port_a_widget._btn_primary, 'Record Success')
    pixmap = grab_widget(window, app)
    targets = {
        'port_a_pill': _widget_rect(window._port_a_widget._pill_act_deact, window),
        'port_a_primary': _widget_rect(window._port_a_widget._btn_primary, window),
        'port_a_cancel': _widget_rect(window._port_a_widget._btn_cancel, window),
    }
    window.close()
    return SceneBuild(content=pixmap, targets=targets)


def _build_qal16_review_fail_retest(app: QApplication, sample: SampleWorkOrder) -> SceneBuild:
    window = _create_base_window(sample)
    _configure_port(
        window._port_a_widget,
        serial=1207,
        sample=sample,
        pressure=502.0,
        primary={'label': 'Retest', 'enabled': True, 'action': 'retest', 'color': 'default'},
        cancel={'label': 'Record Failure', 'enabled': True, 'action': 'record_failure'},
        activation=418.0,
        deactivation=502.0,
        in_spec=False,
    )

    _assert_text(window._port_a_widget._btn_primary, 'Retest')
    _assert_text(window._port_a_widget._btn_cancel, 'Record Failure')
    pixmap = grab_widget(window, app)
    targets = {
        'port_a_pill': _widget_rect(window._port_a_widget._pill_act_deact, window),
        'port_a_primary': _widget_rect(window._port_a_widget._btn_primary, window),
        'port_a_cancel': _widget_rect(window._port_a_widget._btn_cancel, window),
    }
    window.close()
    return SceneBuild(content=pixmap, targets=targets)


def _build_qal16_review_final_failure(app: QApplication, sample: SampleWorkOrder) -> SceneBuild:
    window = _create_base_window(sample)
    _configure_port(
        window._port_a_widget,
        serial=1207,
        sample=sample,
        pressure=506.0,
        primary={'label': 'Record Failure', 'enabled': True, 'action': 'record_failure', 'color': 'green'},
        cancel={'label': 'Retest', 'enabled': True, 'action': 'retest'},
        activation=424.0,
        deactivation=506.0,
        in_spec=False,
    )

    _assert_text(window._port_a_widget._btn_primary, 'Record Failure')
    _assert_text(window._port_a_widget._btn_cancel, 'Retest')
    pixmap = grab_widget(window, app)
    targets = {
        'port_a_pill': _widget_rect(window._port_a_widget._pill_act_deact, window),
        'port_a_primary': _widget_rect(window._port_a_widget._btn_primary, window),
        'port_a_cancel': _widget_rect(window._port_a_widget._btn_cancel, window),
    }
    window.close()
    return SceneBuild(content=pixmap, targets=targets)


def _qal15_scenes() -> list[SceneSpec]:
    return [
        SceneSpec(
            id='01_open_program',
            step_number=1,
            slug='open_program',
            title='Open the Stinger program',
            caption='Launch Stinger and confirm the operator login window appears before entering setup details.',
            callouts=(
                CalloutSpec('operator_id', 'Scan or enter the operator ID first.', 'bottom'),
                CalloutSpec('shop_order', 'Enter the shop order so Stinger can load the part details.', 'bottom'),
                CalloutSpec('login_button', 'Login stays disabled until the required setup fields validate.', 'bottom'),
            ),
            window_kind='dialog_overlay',
            build_state=_build_qal15_login_open,
            filename_stem='qal15_wtl01460_setup_01_open_program',
        ),
        SceneSpec(
            id='02_login_validated',
            step_number=2,
            slug='login_validated',
            title='Validate the work order',
            caption='After validation, Part ID and Sequence auto-populate and the Login button becomes available.',
            callouts=(
                CalloutSpec('part_id', 'The validated Part ID should auto-populate from the work order.', 'right'),
                CalloutSpec('sequence', 'Sequence confirms the calibration workflow that will run.', 'right'),
                CalloutSpec('login_button', 'Use Login only after the validated details appear.', 'bottom'),
            ),
            window_kind='dialog',
            build_state=_build_qal15_login_validated,
            filename_stem='qal15_wtl01460_setup_02_login_validated',
        ),
        SceneSpec(
            id='03_station_ready',
            step_number=3,
            slug='station_ready',
            title='Review the station before calibration',
            caption='Operator, work order, PTP, and ready-to-pressurize controls should be visible before calibration begins.',
            callouts=(
                CalloutSpec('process', 'Process should read QAL15 for the calibration workflow.', 'bottom'),
                CalloutSpec('ptp_setpoint', 'Confirm the loaded PTP setpoint matches the part you are calibrating.', 'bottom'),
                CalloutSpec('port_a_primary', 'Pressurize starts the QAL15 setup path for the selected port.', 'left'),
            ),
            window_kind='main',
            build_state=_build_qal15_station_ready,
            filename_stem='qal15_wtl01460_setup_03_station_ready',
        ),
    ]


def _qal16_scenes() -> list[SceneSpec]:
    return [
        SceneSpec(
            id='01_login_open',
            step_number=1,
            slug='login_open',
            title='Open Stinger and start a QAL 16 session',
            caption='The operator login dialog should appear first so the welded SPS work order can be loaded.',
            callouts=(
                CalloutSpec('operator_id', 'Enter the operator ID for the documentation session.', 'bottom'),
                CalloutSpec('shop_order', 'Enter the welded SPS shop order to load the QAL16 context.', 'bottom'),
                CalloutSpec('login_button', 'Login remains disabled until the work order validates.', 'bottom'),
            ),
            window_kind='dialog_overlay',
            build_state=_build_qal16_login_open,
            filename_stem='qal16_01_login_open',
        ),
        SceneSpec(
            id='02_login_validated',
            step_number=2,
            slug='login_validated',
            title='Confirm the validated work order details',
            caption='The validated shop order should fill in the part and sequence details before the operator proceeds.',
            callouts=(
                CalloutSpec('part_id', 'Part ID should match the welded SPS being checked.', 'right'),
                CalloutSpec('sequence', 'Sequence identifies the QAL16 calibration-check setup.', 'right'),
                CalloutSpec('login_button', 'Login is available only after validation succeeds.', 'bottom'),
            ),
            window_kind='dialog',
            build_state=_build_qal16_login_validated,
            filename_stem='qal16_02_login_validated',
        ),
        SceneSpec(
            id='03_ready_to_test',
            step_number=3,
            slug='ready_to_test',
            title='Verify the station is ready to test',
            caption='QAL16 should be visible in the work-order header and the port should be ready to start with Test.',
            callouts=(
                CalloutSpec('process', 'Process must show QAL16 before capturing calibration-check screenshots.', 'bottom'),
                CalloutSpec('ptp_setpoint', 'Reference the loaded PTP setpoint and direction before testing.', 'bottom'),
                CalloutSpec('port_a_primary', 'QAL16 starts with Test instead of the QAL15 manual-adjust path.', 'left'),
            ),
            window_kind='main',
            build_state=_build_qal16_ready_to_test,
            filename_stem='qal16_03_ready_to_test',
        ),
        SceneSpec(
            id='04_cycling_in_progress',
            step_number=4,
            slug='cycling_in_progress',
            title='Show proof cycling in progress',
            caption='During QAL16 proof cycling, the active port should clearly show the running state and cancellation control.',
            callouts=(
                CalloutSpec('port_a_card', 'Use a single active port so the operator focus stays on the current SPS.', 'right'),
                CalloutSpec('port_a_primary', 'Cycling… indicates the automatic proof-cycle step is running.', 'left'),
                CalloutSpec('port_a_cancel', 'Cancel is the operator exit path while cycling is active.', 'left'),
            ),
            window_kind='main',
            build_state=_build_qal16_cycling,
            filename_stem='qal16_04_cycling_in_progress',
        ),
        SceneSpec(
            id='05_precision_test_in_progress',
            step_number=5,
            slug='precision_test_in_progress',
            title='Capture the precision test sweep',
            caption='Show the live test state with the active sweep, the current ACT/DEACT tracking, and the running control.',
            callouts=(
                CalloutSpec('port_a_chart', 'The pressure chart should show the active sweep through the expected band.', 'right'),
                CalloutSpec('port_a_pill', 'ACT and DEACT values update as the precision test collects switching points.', 'top'),
                CalloutSpec('port_a_primary', 'Testing… keeps the operator informed that the sweep is still active.', 'left'),
            ),
            window_kind='main',
            build_state=_build_qal16_precision_test,
            filename_stem='qal16_05_precision_test_in_progress',
        ),
        SceneSpec(
            id='06_review_pass',
            step_number=6,
            slug='review_pass',
            title='Show a passing review state',
            caption='A passing SPS should show in-spec ACT/DEACT results with Record Success as the recommended action.',
            callouts=(
                CalloutSpec('port_a_pill', 'Passing ACT and DEACT results stay visible for the final operator check.', 'top'),
                CalloutSpec('port_a_primary', 'Record Success is the primary action for an in-spec QAL16 result.', 'left'),
                CalloutSpec('port_a_cancel', 'Retest remains available if the operator wants to rerun the same unit.', 'left'),
            ),
            window_kind='main',
            build_state=_build_qal16_review_pass,
            filename_stem='qal16_06_review_pass',
        ),
        SceneSpec(
            id='07_review_fail_retest',
            step_number=7,
            slug='review_fail_retest',
            title='Show a failed review with retest recommended',
            caption='On an early failed attempt, Retest should stay primary while Record Failure remains available as the alternate action.',
            callouts=(
                CalloutSpec('port_a_pill', 'Out-of-band ACT/DEACT values should be visible for the failed attempt.', 'top'),
                CalloutSpec('port_a_primary', 'Retest is the recommended action on attempts one and two.', 'left'),
                CalloutSpec('port_a_cancel', 'Record Failure stays available if the operator needs to end the attempt.', 'left'),
            ),
            window_kind='main',
            build_state=_build_qal16_review_fail_retest,
            filename_stem='qal16_07_review_fail_retest',
        ),
        SceneSpec(
            id='08_review_final_failure',
            step_number=8,
            slug='review_final_failure',
            title='Show the final-failure decision state',
            caption='On the final failed attempt, Record Failure moves to the primary position while Retest becomes the override.',
            callouts=(
                CalloutSpec('port_a_pill', 'Keep the failed ACT/DEACT values visible so the reason for failure is obvious.', 'top'),
                CalloutSpec('port_a_primary', 'Record Failure becomes the primary action on the final allowed attempt.', 'left'),
                CalloutSpec('port_a_cancel', 'Retest remains available only as the override path.', 'left'),
            ),
            window_kind='main',
            build_state=_build_qal16_review_final_failure,
            filename_stem='qal16_08_review_final_failure',
        ),
    ]
