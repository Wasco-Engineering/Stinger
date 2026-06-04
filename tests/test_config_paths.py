"""Tests for machine-local config path resolution."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core import paths


def test_config_dir_uses_legacy_stand_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / 'StingerHome'
    legacy = home / 'STINGER_99'
    legacy.mkdir(parents=True)
    (legacy / 'stinger_config.yaml').write_text('app:\n  name: legacy\n', encoding='utf-8')
    empty_install = tmp_path / 'no_install'
    empty_install.mkdir()
    monkeypatch.setattr(paths, 'get_install_root', lambda: empty_install)
    monkeypatch.setenv('STINGER_HOME', str(home))
    monkeypatch.setenv('STINGER_STAND_ID', 'STINGER_99')
    monkeypatch.delenv('STINGER_CONFIG_DIR', raising=False)
    assert paths.get_config_dir() == legacy.resolve()


def test_explicit_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / 'cfg'
    cfg.mkdir()
    monkeypatch.setenv('STINGER_CONFIG_DIR', str(cfg))
    assert paths.get_config_dir() == cfg.resolve()


def test_config_dir_prefers_install_root_yaml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install = tmp_path / 'install'
    install.mkdir()
    (install / 'stinger_config.yaml').write_text('app:\n  name: install\n', encoding='utf-8')
    monkeypatch.setattr(paths, 'get_install_root', lambda: install)
    monkeypatch.delenv('STINGER_CONFIG_DIR', raising=False)
    monkeypatch.setenv('STINGER_HOME', str(tmp_path / 'legacy_home'))
    monkeypatch.setenv('STINGER_STAND_ID', 'STINGER_99')
    assert paths.get_config_dir() == install.resolve()


def test_stinger_config_prefers_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / 'local'
    cfg.mkdir()
    local_file = cfg / 'stinger_config.yaml'
    local_file.write_text('app:\n  name: local\n', encoding='utf-8')
    monkeypatch.setenv('STINGER_CONFIG_DIR', str(cfg))
    monkeypatch.delenv('STINGER_CONFIG', raising=False)
    assert paths.get_stinger_config_path() == local_file.resolve()
