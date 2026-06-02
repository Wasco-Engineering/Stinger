"""
Resolve machine-local vs shared (release) paths for Stinger.

Shared builds (code, releases, docs) may live on Z: or git.
Per-PC configs, logs, and calibration state live under STINGER_HOME / STINGER_CONFIG_DIR.

Environment variables (highest precedence first for config files):

  STINGER_CONFIG          Full path to stinger_config.yaml
  STINGER_QUALITY_CONFIG  Full path to quality_cal_config.yaml
  STINGER_CONFIG_DIR      Directory containing both YAML files (recommended per stand)
  STINGER_HOME            Machine-local root (default: %%LOCALAPPDATA%%\\Stinger)
  STINGER_STAND_ID        Subfolder under STINGER_HOME (e.g. STINGER_01, STINGER_02)
  STINGER_RELEASE_ROOT    Shared release/build root on Z: (documentation / deploy scripts)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# Shared engineering root (computer-agnostic artifacts, docs, release bundles)
DEFAULT_RELEASE_ROOT = Path(
    r'Z:\Engineering\Program Builds\Python Builds\Stinger',
)


def get_release_root() -> Path:
    raw = os.environ.get('STINGER_RELEASE_ROOT', '').strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_RELEASE_ROOT


def get_stinger_home() -> Path:
    raw = os.environ.get('STINGER_HOME', '').strip()
    if raw:
        return Path(raw).expanduser().resolve()
    local_app = os.environ.get('LOCALAPPDATA', '').strip()
    if local_app:
        return Path(local_app) / 'Stinger'
    return Path.home() / '.stinger'


def get_stand_id() -> str:
    return os.environ.get('STINGER_STAND_ID', 'default').strip() or 'default'


def get_config_dir() -> Path:
    """Per-stand directory for stinger_config.yaml and quality_cal_config.yaml."""
    raw = os.environ.get('STINGER_CONFIG_DIR', '').strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return get_stinger_home() / get_stand_id()


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _resolve_named_config(
    env_var: str,
    filename: str,
    *,
    frozen_basename: Optional[str] = None,
) -> Path:
    explicit = os.environ.get(env_var, '').strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    local_path = get_config_dir() / filename
    if local_path.is_file():
        return local_path

    if getattr(sys, 'frozen', False):
        frozen_name = frozen_basename or filename
        exe_dir = Path(sys.executable).resolve().parent / frozen_name
        if exe_dir.is_file():
            return exe_dir

    repo_path = _repo_root() / filename
    if repo_path.is_file():
        return repo_path

    return local_path


def get_stinger_config_path() -> Path:
    return _resolve_named_config('STINGER_CONFIG', 'stinger_config.yaml')


def get_quality_cal_config_path() -> Path:
    return _resolve_named_config('STINGER_QUALITY_CONFIG', 'quality_cal_config.yaml')


def get_logs_dir() -> Path:
    return get_config_dir() / 'logs'


def ensure_config_dir() -> Path:
    path = get_config_dir()
    path.mkdir(parents=True, exist_ok=True)
    (path / 'logs').mkdir(parents=True, exist_ok=True)
    return path
