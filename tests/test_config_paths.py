"""Tests for machine-local config path resolution."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core import paths


def test_config_dir_uses_stand_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / 'StingerHome'
    monkeypatch.setenv('STINGER_HOME', str(home))
    monkeypatch.setenv('STINGER_STAND_ID', 'STINGER_99')
    monkeypatch.delenv('STINGER_CONFIG_DIR', raising=False)
    assert paths.get_config_dir() == home / 'STINGER_99'


def test_explicit_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / 'cfg'
    cfg.mkdir()
    monkeypatch.setenv('STINGER_CONFIG_DIR', str(cfg))
    assert paths.get_config_dir() == cfg.resolve()


def test_stinger_config_prefers_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / 'local'
    cfg.mkdir()
    local_file = cfg / 'stinger_config.yaml'
    local_file.write_text('app:\n  name: local\n', encoding='utf-8')
    monkeypatch.setenv('STINGER_CONFIG_DIR', str(cfg))
    monkeypatch.delenv('STINGER_CONFIG', raising=False)
    assert paths.get_stinger_config_path() == local_file.resolve()
