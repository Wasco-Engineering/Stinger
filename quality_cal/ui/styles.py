"""Shared styling for the quality calibration wizard. Uses main Stinger design system."""

from __future__ import annotations

from app.ui.styles import (
    COLORS,
    RADIUS,
    STYLES,
    TYPOGRAPHY,
)

# Re-export for pages that need table_widget, progress_bar, card, etc.
__all__ = [
    "APP_STYLESHEET",
    "COLORS",
    "neutral_badge_style",
    "status_badge_style",
    "STYLES",
]

# Build wizard-wide stylesheet from shared palette so quality cal matches main Stinger
APP_STYLESHEET = f"""
QWidget {{
    background: {COLORS['bg_surface_0']};
    color: {COLORS['text_primary']};
    {TYPOGRAPHY['body']}
}}
QWizard {{
    background: {COLORS['bg_surface_0']};
}}
QLabel[role="eyebrow"] {{
    color: {COLORS['accent_blue']};
    {TYPOGRAPHY['caption']}
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}}
QLabel[role="heroTitle"] {{
    color: {COLORS['text_primary']};
    {TYPOGRAPHY['headline']}
    font-weight: 700;
}}
QLabel[role="heroBody"] {{
    color: {COLORS['text_secondary']};
    {TYPOGRAPHY['body']}
}}
QFrame[card="true"] {{
    background: {COLORS['bg_surface_1']};
    border: 1px solid {COLORS['border_subtle']};
    border-radius: {RADIUS['large']};
}}
QFrame[panel="soft"] {{
    background: {COLORS['bg_surface_2']};
    border: 1px solid {COLORS['border_subtle']};
    border-radius: {RADIUS['xlarge']};
}}
QLineEdit {{
    background: {COLORS['bg_surface_1']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border_muted']};
    border-radius: {RADIUS['medium']};
    padding: 8px 12px;
    {TYPOGRAPHY['body']}
}}
QLineEdit:focus {{
    border: 1px solid {COLORS['accent_blue']};
}}
QLineEdit:disabled {{
    background: {COLORS['button_disabled']};
    color: {COLORS['muted']};
}}
QPushButton {{
    background: {COLORS['button_default']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border_subtle']};
    border-radius: {RADIUS['medium']};
    padding: 8px 16px;
    font-weight: bold;
}}
QPushButton:hover {{
    background: {COLORS['button_hover']};
    border: 1px solid {COLORS['border_muted']};
}}
QPushButton:pressed {{
    background: {COLORS['button_active']};
}}
QPushButton:disabled {{
    background: {COLORS['button_disabled']};
    color: {COLORS['muted']};
    opacity: 0.5;
}}
QPushButton#primaryButton {{
    background: {COLORS['accent_blue']};
    color: white;
    border: 1px solid {COLORS['accent_blue_hover']};
}}
QPushButton#primaryButton:hover {{
    background: {COLORS['accent_blue_hover']};
}}
QPushButton#primaryButton:pressed {{
    background: {COLORS['accent_blue_active']};
}}
QCheckBox {{
    spacing: 10px;
    color: {COLORS['text_primary']};
    {TYPOGRAPHY['body']}
}}
QCheckBox::indicator {{
    width: 20px;
    height: 20px;
}}
QFrame[panelRole="card"] {{
    background: {COLORS['bg_surface_1']};
    border: 1px solid {COLORS['border_subtle']};
    border-radius: {RADIUS['large']};
}}
QFrame[panelRole="soft"] {{
    background: {COLORS['bg_surface_2']};
    border: 1px solid {COLORS['border_subtle']};
    border-radius: {RADIUS['medium']};
}}
QLabel[textRole="hero"] {{
    color: {COLORS['text_primary']};
    {TYPOGRAPHY['headline']}
    font-weight: 700;
}}
QLabel[textRole="sectionTitle"] {{
    color: {COLORS['text_primary']};
    {TYPOGRAPHY['title']}
    font-weight: 700;
}}
QLabel[textRole="subsectionTitle"] {{
    color: {COLORS['text_primary']};
    {TYPOGRAPHY['subtitle']}
    font-weight: 700;
}}
QLabel[textRole="statusTitle"] {{
    color: {COLORS['text_primary']};
    {TYPOGRAPHY['subtitle']}
    font-weight: 700;
}}
QLabel[textRole="body"] {{
    color: {COLORS['text_secondary']};
    {TYPOGRAPHY['body']}
}}
QLabel[textRole="muted"] {{
    color: {COLORS['muted']};
    {TYPOGRAPHY['caption']}
}}
QLabel[textRole="stageTitle"] {{
    color: {COLORS['text_primary']};
    {TYPOGRAPHY['body']}
    font-weight: 700;
}}
QLabel[textRole="stageDescription"] {{
    color: {COLORS['muted']};
    {TYPOGRAPHY['caption']}
}}
QLabel[textRole="metric"] {{
    color: {COLORS['text_primary']};
    {TYPOGRAPHY['body']}
    font-weight: 600;
}}
QLabel[chipState="success"] {{
    background: {COLORS['success_muted']};
    color: {COLORS['success']};
    border: 1px solid {COLORS['success']};
    border-radius: {RADIUS['pill']};
    padding: 6px 10px;
    font-weight: 700;
}}
QLabel[chipState="danger"] {{
    background: {COLORS['danger_muted']};
    color: {COLORS['danger']};
    border: 1px solid {COLORS['danger']};
    border-radius: {RADIUS['pill']};
    padding: 6px 10px;
    font-weight: 700;
}}
QFrame[hardwareState="ok"] {{
    border-left: 4px solid {COLORS['success']};
}}
QFrame[hardwareState="error"] {{
    border-left: 4px solid {COLORS['danger']};
}}
QFrame[stageState="current"] {{
    border: 1px solid {COLORS['accent_blue']};
    background: {COLORS['accent_blue_muted']};
}}
QFrame[stageState="complete"] {{
    border: 1px solid {COLORS['success']};
    background: {COLORS['success_muted']};
}}
QFrame[stageState="pending"] {{
    border: 1px solid {COLORS['border_subtle']};
}}
QLabel[badgeState="current"] {{
    background: {COLORS['accent_blue']};
    color: {COLORS['white']};
    border-radius: 14px;
    font-weight: 700;
}}
QLabel[badgeState="complete"] {{
    background: {COLORS['success']};
    color: {COLORS['white']};
    border-radius: 14px;
    font-weight: 700;
}}
QLabel[badgeState="pending"] {{
    background: {COLORS['bg_surface_2']};
    color: {COLORS['muted']};
    border-radius: 14px;
    border: 1px solid {COLORS['border_muted']};
    font-weight: 700;
}}
QFrame[bannerState="success"] {{
    background: {COLORS['success_muted']};
    border: 1px solid {COLORS['success']};
    border-radius: {RADIUS['large']};
}}
QFrame[bannerState="danger"] {{
    background: {COLORS['danger_muted']};
    border: 1px solid {COLORS['danger']};
    border-radius: {RADIUS['large']};
}}
QFrame[bannerState="neutral"] {{
    background: {COLORS['bg_surface_2']};
    border: 1px solid {COLORS['border_muted']};
    border-radius: {RADIUS['large']};
}}
QTableWidget {{
    background: {COLORS['bg_surface_1']};
    alternate-background-color: {COLORS['bg_surface_2']};
    gridline-color: {COLORS['border_subtle']};
    border: 1px solid {COLORS['border_subtle']};
    border-radius: {RADIUS['medium']};
}}
QHeaderView::section {{
    background: {COLORS['bg_surface_2']};
    color: {COLORS['text_secondary']};
    padding: 8px 6px;
    border: none;
    border-bottom: 1px solid {COLORS['border_muted']};
    font-weight: 700;
}}
QSpinBox {{
    background: {COLORS['bg_surface_1']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border_muted']};
    border-radius: {RADIUS['medium']};
    padding: 6px 8px;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QRadioButton {{
    spacing: 10px;
    color: {COLORS['text_primary']};
    font-weight: 600;
}}
{STYLES['progress_bar']}
"""


def status_badge_style(ok: bool) -> str:
    """Pill-style badge for Ready (ok=True) or Check/Fail (ok=False). Uses shared COLORS."""
    if ok:
        bg = COLORS["success_muted"]
        fg = COLORS["success"]
        border = COLORS["success"]
    else:
        bg = COLORS["danger_muted"]
        fg = COLORS["danger"]
        border = COLORS["danger"]
    return (
        f"background: {bg}; color: {fg}; border: 1px solid {border}; "
        f"border-radius: {RADIUS['pill']}; padding: 8px 14px; font-weight: 700; min-width: 84px;"
    )


def neutral_badge_style() -> str:
    """Pill-style badge for neutral/checking state."""
    return (
        f"background: {COLORS['bg_surface_2']}; color: {COLORS['muted']}; "
        f"border: 1px solid {COLORS['border_muted']}; "
        f"border-radius: {RADIUS['pill']}; padding: 8px 14px; font-weight: 700; min-width: 84px;"
    )
