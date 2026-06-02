"""Tests for Alicat pressure error-model correction."""

from __future__ import annotations

import pytest

from app.hardware.alicat import AlicatController
from app.services.pressure_calibration import apply_error_model


def test_alicat_applies_configured_error_model() -> None:
    model = {
        'type': 'piecewise_linear',
        'segments': [
            {'max_psi': None, 'slope_error_per_psi': 0.0, 'intercept_error_psi': 0.5},
        ],
    }
    ctrl = AlicatController(
        {
            'com_port': 'COM99',
            'address': 'A',
            'alicat_error_model': model,
        }
    )
    raw = 10.0
    expected = apply_error_model(raw, model)
    assert expected == pytest.approx(9.5)
    assert ctrl._error_model is model
