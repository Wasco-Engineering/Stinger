#!/usr/bin/env python3
"""Merge transducer (and optional Alicat) error models into local stinger_config.yaml."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import save_config, load_config
from app.core.paths import get_stinger_config_path
from quality_cal.core.calibration_export import merge_hardware_into_stinger_config

DEFAULT_MODELS = (
    PROJECT_ROOT
    / 'scripts/data/offset_validation_20260210/offline_optimizer_run/recommended_calibration.yaml'
)


def main() -> int:
    parser = argparse.ArgumentParser(description='Apply calibration YAML to local stinger config.')
    parser.add_argument('--models', type=Path, default=DEFAULT_MODELS)
    parser.add_argument('--config', type=Path, default=None, help='Override stinger config path')
    args = parser.parse_args()

    if not args.models.is_file():
        print(f'ERROR: models file not found: {args.models}', flush=True)
        return 1

    snippet = yaml.safe_load(args.models.read_text(encoding='utf-8'))
    if not isinstance(snippet, dict):
        print('ERROR: models file must be a mapping', flush=True)
        return 1

    config_path = args.config or get_stinger_config_path()
    if config_path.is_file():
        stinger = load_config(config_path)
    else:
        print(f'ERROR: config not found: {config_path}', flush=True)
        return 1

    merged = merge_hardware_into_stinger_config(stinger, snippet)
    hw = merged.get('hardware', {}).get('labjack', {})
    for port_key in ('port_a', 'port_b'):
        port_cfg = hw.get(port_key, {})
        port_cfg['transducer_installed'] = True
        port_cfg['transducer_reference'] = 'absolute'
        port_cfg['transducer_pressure_max'] = 30.0
        hw[port_key] = port_cfg
    merged['hardware']['labjack'] = hw

    save_config(merged, config_path)
    print(f'Applied models to {config_path}', flush=True)
    for port_key in ('port_a', 'port_b'):
        model = hw.get(port_key, {}).get('transducer_error_model')
        print(f'  {port_key}: transducer_error_model={"yes" if model else "no"}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
