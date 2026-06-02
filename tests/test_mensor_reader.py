"""Tests for Mensor pressure parsing."""

from __future__ import annotations

from quality_cal.core.mensor_reader import MensorReader


def test_parse_scientific_psia_field() -> None:
    assert MensorReader._parse_pressure('+1.34419E+01') == 13.4419


def test_parse_comma_separated_scientific() -> None:
    assert MensorReader._parse_pressure('0.159,+1.09813E+01,other') == 10.9813


def test_parse_legacy_e_prefix_field() -> None:
    assert MensorReader._parse_pressure('E+1.09813E+01') == 10.9813
