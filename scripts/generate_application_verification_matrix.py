"""Generate the Stinger application verification matrix from PTP."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_config
from app.database.models import ProductTestParameters
from app.database.session import close_database, get_engine, initialize_database, session_scope
from app.services.ptp_service import (
    TestSetup,
    derive_test_setup,
    load_ptp_from_db,
    load_ptp_from_dump,
    validate_ptp_params,
)
from app.services.ptp_switch_resolver import PtpSwitchResolution, resolve_ptp_switch_config
from app.services.sweep_utils import resolve_sweep_mode


DEFAULT_OUTPUT = PROJECT_ROOT / 'docs' / 'application_verification_matrix.csv'
RECENT_APPLICATIONS = (
    ('SPS01439-02', '300'),
    ('SPS01439-02', '600'),
    ('SPS01496-02', '300'),
    ('SPS01496-02', '600'),
    ('SPS02209-02', '300'),
    ('SPS02209-02', '600'),
)
TRACKING_FIELDS = (
    'bench_status',
    'last_verified_at',
    'verified_by',
    'stand_id',
    'notes',
)
HEADERS = (
    'part_id',
    'sequence_id',
    'ptp_source',
    'units_label',
    'pressure_reference',
    'target_activation_direction',
    'activation_target',
    'increasing_lower',
    'increasing_upper',
    'decreasing_lower',
    'decreasing_upper',
    'reset_lower',
    'reset_upper',
    'common_terminal',
    'normally_open_terminal',
    'normally_closed_terminal',
    'sweep_mode',
    'reference_interpretation',
    'port_a_mapping',
    'port_a_derivation_mode',
    'port_b_mapping',
    'port_b_derivation_mode',
    'validation_status',
    'validation_notes',
    *TRACKING_FIELDS,
)


def parse_application(value: str) -> tuple[str, str]:
    if ':' in value:
        part, sequence = value.split(':', 1)
    elif '/' in value:
        part, sequence = value.split('/', 1)
    else:
        raise argparse.ArgumentTypeError('Use PART:SEQUENCE, for example SPS01496-02:300')
    part = part.strip()
    sequence = sequence.strip()
    if not part or not sequence:
        raise argparse.ArgumentTypeError('Application requires both part and sequence')
    return part, sequence


def build_matrix_rows(
    applications: Iterable[tuple[str, str]],
    config: dict[str, Any],
    existing_tracking: Optional[dict[tuple[str, str], dict[str, str]]] = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for part_id, sequence_id in sorted({_normalize_app(app) for app in applications}):
        params, source = _load_ptp(part_id, sequence_id)
        tracking = (existing_tracking or {}).get((part_id, sequence_id), {})
        rows.append(build_matrix_row(part_id, sequence_id, params, source, config, tracking))
    return rows


def build_matrix_row(
    part_id: str,
    sequence_id: str,
    params: dict[str, Any],
    source: str,
    config: dict[str, Any],
    tracking: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    row = {header: '' for header in HEADERS}
    row.update({'part_id': part_id, 'sequence_id': sequence_id, 'ptp_source': source})
    for field in TRACKING_FIELDS:
        row[field] = (tracking or {}).get(field, '')

    if not params:
        row['validation_status'] = 'ERROR'
        row['validation_notes'] = 'No PTP parameters found'
        return row

    is_valid, errors = validate_ptp_params({str(k): str(v) for k, v in params.items()})
    setup: Optional[TestSetup] = None
    if is_valid:
        setup = derive_test_setup(part_id, sequence_id, params)
        _fill_setup_fields(row, setup)
        row['sweep_mode'] = resolve_sweep_mode(setup, atmosphere_psi=14.7)
        row['reference_interpretation'] = _reference_interpretation(setup)
    else:
        row['validation_status'] = 'ERROR'
        row['validation_notes'] = '; '.join(errors)

    resolutions = {
        'port_a': _resolve_port(params, 'port_a', config),
        'port_b': _resolve_port(params, 'port_b', config),
    }
    resolution_errors: list[str] = []
    for port_id, resolution in resolutions.items():
        row[f'{port_id}_mapping'] = resolution.summary if resolution.is_valid else 'ERROR'
        row[f'{port_id}_derivation_mode'] = resolution.derivation_mode
        if resolution.errors:
            resolution_errors.append(f'{port_id}: ' + '; '.join(resolution.errors))

    if not row['validation_status']:
        row['validation_status'] = 'OK' if not resolution_errors else 'ERROR'
        row['validation_notes'] = '; '.join(resolution_errors)
    elif resolution_errors:
        row['validation_notes'] = '; '.join(filter(None, [row['validation_notes'], *resolution_errors]))

    return row


def read_existing_tracking(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    if not path.exists():
        return {}
    with path.open('r', encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        tracking: dict[tuple[str, str], dict[str, str]] = {}
        for row in reader:
            key = (
                str(row.get('part_id', '')).strip(),
                _normalize_sequence(str(row.get('sequence_id', '')).strip()),
            )
            if not key[0] or not key[1]:
                continue
            tracking[key] = {field: str(row.get(field, '') or '') for field in TRACKING_FIELDS}
        return tracking


def write_matrix(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(HEADERS), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def discover_sps_applications() -> list[tuple[str, str]]:
    if get_engine() is None:
        raise RuntimeError('Database is not initialized; --all-sps requires database access')
    applications: set[tuple[str, str]] = set()
    with session_scope() as session:
        records = (
            session.query(ProductTestParameters.PartID, ProductTestParameters.SequenceID)
            .filter(ProductTestParameters.PartID.like('SPS%'))
            .distinct()
            .all()
        )
        for part_id, sequence_id in records:
            part = str(part_id or '').strip()
            sequence = _normalize_sequence(str(sequence_id or '').strip())
            if part and sequence:
                applications.add((part, sequence))
    return sorted(applications)


def _fill_setup_fields(row: dict[str, str], setup: TestSetup) -> None:
    row.update(
        {
            'units_label': str(setup.units_label or ''),
            'pressure_reference': str(setup.pressure_reference or ''),
            'target_activation_direction': str(setup.activation_direction or ''),
            'activation_target': _fmt(setup.activation_target),
            'common_terminal': _fmt(setup.terminals.get('common')),
            'normally_open_terminal': _fmt(setup.terminals.get('normally_open')),
            'normally_closed_terminal': _fmt(setup.terminals.get('normally_closed')),
        }
    )
    for prefix, band_name in (
        ('increasing', 'increasing'),
        ('decreasing', 'decreasing'),
        ('reset', 'reset'),
    ):
        band = setup.bands.get(band_name, {})
        row[f'{prefix}_lower'] = _fmt(band.get('lower'))
        row[f'{prefix}_upper'] = _fmt(band.get('upper'))


def _resolve_port(
    params: dict[str, Any],
    port_id: str,
    config: dict[str, Any],
) -> PtpSwitchResolution:
    labjack = config.get('hardware', {}).get('labjack', {})
    base = {key: value for key, value in labjack.items() if key not in {'port_a', 'port_b'}}
    port_config = {**base, **labjack.get(port_id, {})}
    return resolve_ptp_switch_config(ptp_params=params, port_id=port_id, port_config=port_config)


def _load_ptp(part_id: str, sequence_id: str) -> tuple[dict[str, str], str]:
    params = load_ptp_from_db(part_id, sequence_id) if get_engine() is not None else {}
    if params:
        return params, 'database'
    params = load_ptp_from_dump(part_id, sequence_id)
    if params:
        return params, 'dump'
    return {}, 'missing'


def _reference_interpretation(setup: TestSetup) -> str:
    sequence = _normalize_sequence(setup.sequence_id)
    reference = (setup.pressure_reference or '').strip().lower()
    if reference == 'gauge':
        return 'gauge: atmosphere reference'
    if sequence == '300':
        return 'absolute package: seq 300 atmosphere-side control'
    if sequence == '600':
        return 'absolute package: seq 600 vacuum-reference control'
    return 'absolute reference'


def _normalize_app(app: tuple[str, str]) -> tuple[str, str]:
    return app[0].strip(), _normalize_sequence(app[1])


def _normalize_sequence(sequence_id: str) -> str:
    try:
        return str(int(str(sequence_id).strip()))
    except (TypeError, ValueError):
        return str(sequence_id or '').strip()


def _fmt(value: Any) -> str:
    if value is None:
        return ''
    return str(value)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--application',
        action='append',
        type=parse_application,
        help='Application to include as PART:SEQUENCE. May be repeated.',
    )
    parser.add_argument('--all-sps', action='store_true', help='Include every SPS application in PTP.')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    config = load_config()
    db_initialized = initialize_database(config.get('database', {}))
    try:
        applications = list(args.application or [])
        if args.all_sps:
            if not db_initialized:
                parser.error('--all-sps requires a database connection')
            applications.extend(discover_sps_applications())
        if not applications:
            applications = list(read_existing_tracking(args.output).keys()) or list(RECENT_APPLICATIONS)
        existing_tracking = read_existing_tracking(args.output)
        rows = build_matrix_rows(applications, config, existing_tracking)
        write_matrix(args.output, rows)
    finally:
        close_database()

    print(f'Wrote {len(rows)} application rows to {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
