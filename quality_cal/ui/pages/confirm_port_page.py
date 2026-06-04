"""Compact confirmation page for moving the Mensor between ports."""

from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWizardPage

from quality_cal.ui.styles import COLORS, TYPOGRAPHY


class ConfirmPortPage(QWizardPage):
    def __init__(self, *, title: str, message: str, parent=None) -> None:
        super().__init__(parent)
        self.setTitle(title)
        self.setSubTitle('Confirm the Mensor connection, then continue.')
        self._confirmed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 24)
        layout.setSpacing(16)

        card = QFrame(self)
        card.setProperty('card', True)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(12)

        label = QLabel(message)
        label.setWordWrap(True)
        label.setStyleSheet(
            f"color: {COLORS['text_primary']}; {TYPOGRAPHY['body']};"
        )
        card_layout.addWidget(label)

        self.mensor_port_label = QLabel('Mensor COM port: —')
        self.mensor_port_label.setWordWrap(True)
        self.mensor_port_label.setStyleSheet(
            f"color: {COLORS['muted']}; {TYPOGRAPHY['caption']};"
        )
        card_layout.addWidget(self.mensor_port_label)

        row = QHBoxLayout()
        row.addStretch(1)
        self.confirm_button = QPushButton('Confirm connection')
        self.confirm_button.setObjectName('primaryButton')
        self.confirm_button.clicked.connect(self._on_confirm)
        row.addWidget(self.confirm_button)
        row.addStretch(1)
        card_layout.addLayout(row)
        layout.addWidget(card)
        layout.addStretch(1)

    def _on_confirm(self) -> None:
        self._confirmed = True
        self.confirm_button.setEnabled(False)
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._confirmed

    def initializePage(self) -> None:
        self._confirmed = False
        self.confirm_button.setEnabled(True)
        wizard = self.wizard()
        if wizard is not None:
            mensor_cfg = wizard.config.get('hardware', {}).get('mensor', {})
            port = mensor_cfg.get('port', '')
            display = str(port).strip() if port else '—'
            self.mensor_port_label.setText(f'Mensor COM port: {display}')
