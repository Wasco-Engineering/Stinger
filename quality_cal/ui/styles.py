"""Styling for the standalone quality calibration app."""

from __future__ import annotations

from app.ui.styles import RADIUS, STYLES, TYPOGRAPHY

# White workspace + dark rail — avoid grey cards on grey background.
COLORS = {
    'text_primary': '#0f172a',
    'text_secondary': '#475569',
    'muted': '#64748b',
    'muted_placeholder': '#94a3b8',
    'white': '#ffffff',
    'bg_workspace': '#ffffff',
    'bg_rail': '#0f172a',
    'bg_rail_muted': '#1e293b',
    'text_on_rail': '#f8fafc',
    'text_on_rail_muted': '#94a3b8',
    'line': '#e2e8f0',
    'line_strong': '#cbd5e1',
    'button_default': '#ffffff',
    'button_hover': '#f8fafc',
    'button_active': '#f1f5f9',
    'button_disabled': '#f8fafc',
    'border': '#cbd5e1',
    'border_subtle': '#e2e8f0',
    'border_muted': '#cbd5e1',
    'accent_blue': '#2563eb',
    'accent_blue_hover': '#1d4ed8',
    'accent_blue_active': '#1e40af',
    'accent_blue_muted': '#eff6ff',
    'success': '#15803d',
    'success_muted': '#ecfdf5',
    'danger': '#b91c1c',
    'danger_muted': '#fef2f2',
    'warning': '#b45309',
    'warning_muted': '#fffbeb',
    'unknown': '#64748b',
    # Legacy aliases used in views
    'bg_surface_0': '#ffffff',
    'bg_surface_1': '#ffffff',
    'bg_surface_2': '#f8fafc',
}

__all__ = [
    'APP_STYLESHEET',
    'COLORS',
    'neutral_badge_style',
    'status_badge_style',
    'STYLES',
]

APP_STYLESHEET = f"""
QMainWindow, QWidget {{
    background: {COLORS['bg_workspace']};
    color: {COLORS['text_primary']};
    {TYPOGRAPHY['body']}
}}
QFrame[panelRole="chrome"] {{
    background: {COLORS['bg_workspace']};
    border: none;
    border-bottom: 1px solid {COLORS['line']};
}}
QFrame[panelRole="footer"] {{
    background: {COLORS['bg_workspace']};
    border: none;
    border-top: 1px solid {COLORS['line']};
}}
QFrame[panelRole="card"] {{
    background: {COLORS['bg_workspace']};
    border: 1px solid {COLORS['line']};
    border-radius: {RADIUS['medium']};
}}
QFrame[panelRole="divider"] {{
    background: {COLORS['line']};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}
QFrame[panelRole="rail"] {{
    background: {COLORS['bg_rail']};
    border: none;
    border-radius: {RADIUS['medium']};
}}
QFrame[panelRole="rail"] QLabel {{
    color: {COLORS['text_on_rail']};
}}
QFrame[panelRole="rail"] QLabel[textRole="sectionTitle"] {{
    color: {COLORS['text_on_rail']};
    font-size: 13px;
    font-weight: 700;
}}
QFrame[panelRole="railStage"] {{
    background: {COLORS['bg_rail_muted']};
    border: 1px solid {COLORS['bg_rail_muted']};
    border-radius: {RADIUS['small']};
}}
QFrame[panelRole="railStage"] QLabel[textRole="stageTitle"] {{
    color: {COLORS['text_on_rail']};
    font-size: 12px;
    font-weight: 600;
}}
QFrame[panelRole="railStage"] QLabel[textRole="stageDescription"] {{
    color: {COLORS['text_on_rail_muted']};
    font-size: 11px;
}}
QFrame[panelRole="railStage"][stageState="current"] {{
    background: {COLORS['bg_rail_muted']};
    border: 2px solid {COLORS['accent_blue']};
}}
QFrame[panelRole="railStage"][stageState="complete"] {{
    background: {COLORS['bg_rail_muted']};
    border: 1px solid {COLORS['success']};
}}
QFrame[panelRole="railStage"][stageState="pending"] {{
    background: {COLORS['bg_rail']};
    border: 1px solid #334155;
}}
QLabel[role="eyebrow"] {{
    color: {COLORS['accent_blue']};
    {TYPOGRAPHY['caption']}
    font-weight: 700;
    letter-spacing: 0.06em;
}}
QLineEdit {{
    background: {COLORS['bg_workspace']};
    border: 1px solid {COLORS['border_muted']};
    border-radius: {RADIUS['small']};
    padding: 5px 8px;
    min-height: 24px;
}}
QLineEdit:focus {{
    border: 2px solid {COLORS['accent_blue']};
    padding: 4px 7px;
}}
QComboBox {{
    background: {COLORS['bg_workspace']};
    border: 1px solid {COLORS['border_muted']};
    border-radius: {RADIUS['small']};
    padding: 5px 8px;
    min-height: 24px;
}}
QComboBox:focus {{
    border: 2px solid {COLORS['accent_blue']};
}}
QPushButton {{
    background: {COLORS['button_default']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border_muted']};
    border-radius: {RADIUS['small']};
    padding: 6px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {COLORS['button_hover']};
}}
QPushButton#primaryButton {{
    background: {COLORS['accent_blue']};
    color: {COLORS['white']};
    border: 1px solid {COLORS['accent_blue_hover']};
}}
QPushButton#primaryButton:hover {{
    background: {COLORS['accent_blue_hover']};
}}
QPushButton[buttonRole="segment"] {{
    background: {COLORS['bg_workspace']};
    border: 1px solid {COLORS['border_muted']};
    border-radius: 0;
    padding: 8px 16px;
}}
QPushButton[buttonRole="segment"]:checked {{
    background: {COLORS['accent_blue']};
    color: {COLORS['white']};
    border-color: {COLORS['accent_blue']};
}}
QPushButton[segmentPos="first"] {{
    border-top-left-radius: {RADIUS['small']};
    border-bottom-left-radius: {RADIUS['small']};
}}
QPushButton[segmentPos="last"] {{
    border-top-right-radius: {RADIUS['small']};
    border-bottom-right-radius: {RADIUS['small']};
}}
QCheckBox {{
    spacing: 6px;
}}
QFrame[statusStrip="ok"] {{
    background: {COLORS['success_muted']};
    border: 1px solid {COLORS['success']};
    border-radius: {RADIUS['small']};
}}
QFrame[statusStrip="bad"] {{
    background: {COLORS['danger_muted']};
    border: 1px solid {COLORS['danger']};
    border-radius: {RADIUS['small']};
}}
QFrame[statusStrip="pending"] {{
    background: {COLORS['accent_blue_muted']};
    border: 1px solid {COLORS['accent_blue']};
    border-radius: {RADIUS['small']};
}}
QLabel[textRole="hero"] {{
    font-size: 20px;
    font-weight: 700;
    color: {COLORS['text_primary']};
}}
QLabel[textRole="sectionTitle"] {{
    font-size: 13px;
    font-weight: 700;
    color: {COLORS['text_primary']};
}}
QLabel[textRole="body"] {{
    font-size: 12px;
    color: {COLORS['text_secondary']};
}}
QLabel[textRole="muted"] {{
    font-size: 11px;
    color: {COLORS['muted']};
}}
QLabel[badgeState="current"] {{
    background: {COLORS['accent_blue']};
    color: {COLORS['white']};
    border-radius: 11px;
    font-weight: 700;
}}
QLabel[badgeState="complete"] {{
    background: {COLORS['success']};
    color: {COLORS['white']};
    border-radius: 11px;
    font-weight: 700;
}}
QLabel[badgeState="pending"] {{
    background: {COLORS['bg_rail_muted']};
    color: {COLORS['text_on_rail_muted']};
    border-radius: 11px;
    font-weight: 700;
}}
QTableWidget {{
    background: {COLORS['bg_workspace']};
    alternate-background-color: {COLORS['bg_surface_2']};
    gridline-color: {COLORS['line']};
    border: 1px solid {COLORS['line']};
    border-radius: {RADIUS['small']};
    font-size: 12px;
}}
QHeaderView::section {{
    background: {COLORS['bg_workspace']};
    color: {COLORS['text_secondary']};
    padding: 5px 8px;
    border: none;
    border-bottom: 2px solid {COLORS['line_strong']};
    font-weight: 700;
    font-size: 11px;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
{STYLES['progress_bar']}
"""


def status_badge_style(ok: bool) -> str:
    if ok:
        bg, fg, border = COLORS['success_muted'], COLORS['success'], COLORS['success']
    else:
        bg, fg, border = COLORS['danger_muted'], COLORS['danger'], COLORS['danger']
    return (
        f'background: {bg}; color: {fg}; border: 1px solid {border}; '
        f'border-radius: {RADIUS["pill"]}; padding: 2px 8px; font-weight: 700; font-size: 11px;'
    )


def rail_stage_frame_style(state: str) -> str:
    border = COLORS['bg_rail_muted']
    background = COLORS['bg_rail_muted']
    if state == 'current':
        border = COLORS['accent_blue']
        background = COLORS['bg_rail_muted']
    elif state == 'complete':
        border = COLORS['success']
    elif state == 'pending':
        border = '#334155'
        background = COLORS['bg_rail']
    return (
        f'QFrame {{ background: {background}; border: 1px solid {border}; '
        f'border-radius: {RADIUS["small"]}; }}'
    )


def rail_label_style(*, role: str = 'title') -> str:
    if role == 'description':
        return f'color: {COLORS["text_on_rail_muted"]}; font-size: 11px; background: transparent;'
    return f'color: {COLORS["text_on_rail"]}; font-size: 12px; font-weight: 600; background: transparent;'


def rail_badge_style(state: str) -> str:
    if state == 'current':
        bg, fg = COLORS['accent_blue'], COLORS['white']
    elif state == 'complete':
        bg, fg = COLORS['success'], COLORS['white']
    else:
        bg, fg = '#334155', COLORS['text_on_rail']
    return (
        f'background: {bg}; color: {fg}; border-radius: 11px; '
        f'font-weight: 700; font-size: 11px; min-width: 22px; min-height: 22px;'
    )


def neutral_badge_style() -> str:
    return (
        f'background: {COLORS["bg_surface_2"]}; color: {COLORS["muted"]}; '
        f'border: 1px solid {COLORS["line"]}; '
        f'border-radius: {RADIUS["pill"]}; padding: 2px 8px; font-weight: 700; font-size: 11px;'
    )
