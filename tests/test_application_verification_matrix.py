from __future__ import annotations

from typing import Any

from scripts import generate_application_verification_matrix as matrix


def _config() -> dict[str, Any]:
    return {
        'hardware': {
            'labjack': {
                'port_a': {'switch_sensed_db9_pins': [3], 'switch_com_state': 0},
                'port_b': {'switch_sensed_db9_pins': [3], 'switch_com_state': 0},
            }
        }
    }


def _ptp(sequence: str) -> dict[str, str]:
    if sequence == '600':
        no_terminal, nc_terminal = '1', '3'
        reference, units = 'Absolute', '21'
    else:
        no_terminal, nc_terminal = '3', '1'
        reference, units = 'Gauge', '19'
    return {
        'ActivationTarget': '400',
        'IncreasingLowerLimit': '-Inf',
        'IncreasingUpperLimit': '490',
        'DecreasingLowerLimit': '390',
        'DecreasingUpperLimit': '410',
        'ResetBandLowerLimit': '-Inf',
        'ResetBandUpperLimit': 'Inf',
        'TargetActivationDirection': 'Decreasing',
        'UnitsOfMeasure': units,
        'PressureReference': reference,
        'CommonTerminal': '4',
        'NormallyOpenTerminal': no_terminal,
        'NormallyClosedTerminal': nc_terminal,
    }


def test_matrix_generator_emits_recent_sps_rows(monkeypatch) -> None:
    def _fake_load(part_id: str, sequence_id: str) -> tuple[dict[str, str], str]:
        assert part_id in {'SPS01496-02', 'SPS02209-02', 'SPS01439-02'}
        return _ptp(sequence_id), 'fixture'

    monkeypatch.setattr(matrix, '_load_ptp', _fake_load)

    rows = matrix.build_matrix_rows(
        [
            ('SPS01496-02', '300'),
            ('SPS02209-02', '300'),
            ('SPS01439-02', '600'),
        ],
        _config(),
    )

    assert [row['part_id'] for row in rows] == ['SPS01439-02', 'SPS01496-02', 'SPS02209-02']
    assert all(row['validation_status'] == 'OK' for row in rows)
    row_300 = next(row for row in rows if row['sequence_id'] == '300')
    row_600 = next(row for row in rows if row['sequence_id'] == '600')
    assert row_300['port_a_derivation_mode'] == 'derive_nc_from_no'
    assert row_600['port_a_derivation_mode'] == 'derive_no_from_nc'
    assert 'atmosphere reference' in row_300['reference_interpretation']
    assert 'vacuum-reference' in row_600['reference_interpretation']


def test_matrix_generator_preserves_tracking_fields(monkeypatch) -> None:
    monkeypatch.setattr(matrix, '_load_ptp', lambda _part, seq: (_ptp(seq), 'fixture'))

    rows = matrix.build_matrix_rows(
        [('SPS01496-02', '300')],
        _config(),
        {
            ('SPS01496-02', '300'): {
                'bench_status': 'working',
                'notes': 'verified on left port',
            }
        },
    )

    assert rows[0]['bench_status'] == 'working'
    assert rows[0]['notes'] == 'verified on left port'
