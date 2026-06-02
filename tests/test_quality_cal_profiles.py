"""Tests for quality-calibration profile scheduling."""

from __future__ import annotations

import pytest

from quality_cal.config import (
    CAL10_WCS02075_PRESSURE_POINTS_PSIA,
    PROFILE_CAL10_WCS02075,
    PROFILE_HIGH_0_115,
    PROFILE_MENSOR_0_30,
    build_pressure_points_for_profile,
    estimate_profile_duration_s,
    load_config,
    parse_quality_settings,
)


def test_cal10_wcs02075_profile_matches_work_instruction_order() -> None:
    config = load_config()
    points = build_pressure_points_for_profile(PROFILE_CAL10_WCS02075, config['quality'])
    assert points == CAL10_WCS02075_PRESSURE_POINTS_PSIA
    settings = parse_quality_settings(config, profile_id=PROFILE_CAL10_WCS02075)
    assert settings.require_mensor is True
    assert len(points) == 10


def test_mensor_0_30_profile_point_count() -> None:
    config = load_config()
    points = build_pressure_points_for_profile(PROFILE_MENSOR_0_30, config['quality'])
    assert 0.0 in points
    assert 30.0 in points
    assert len(points) == 31


def test_high_0_115_profile_includes_high_range() -> None:
    config = load_config()
    points = build_pressure_points_for_profile(PROFILE_HIGH_0_115, config['quality'])
    assert 30.0 in points
    assert 115.0 in points
    assert any(p > 30.0 for p in points)


def test_parse_quality_settings_profile_switch() -> None:
    config = load_config()
    low = parse_quality_settings(config, profile_id=PROFILE_MENSOR_0_30)
    high = parse_quality_settings(config, profile_id=PROFILE_HIGH_0_115)
    assert low.profile_id == PROFILE_MENSOR_0_30
    assert high.profile_id == PROFILE_HIGH_0_115
    assert high.prompt_disconnect_mensor_above_psi == pytest.approx(30.0)
    assert len(high.pressure_points_psia) > len(low.pressure_points_psia)
    assert estimate_profile_duration_s(high) > estimate_profile_duration_s(low)
