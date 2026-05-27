from __future__ import annotations

from app.database import operations


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeConnection:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql, _params):
        return _FakeResult(self._row)

    def exec_driver_sql(self, _sql):
        return None


class _FakeEngine:
    def __init__(self, row):
        self._row = row
        self.disposed = False

    def connect(self):
        return _FakeConnection(self._row)

    def dispose(self):
        self.disposed = True


def test_lookup_shop_order_returns_normalized_details(monkeypatch) -> None:
    engine = _FakeEngine({
        'ORDNUM_147': '51034643',
        'PRTNUM_147': 'CERBERUS-575T-SEI        ',
        'CURQTY_147': 40.0,
        'CURDUE_147': None,
        'STATUS_147': ' ',
        'ALTBOM_147': ' BOM1 ',
        'ALTRTG_147': ' RTG1 ',
    })
    monkeypatch.setattr(operations, '_load_runtime_config', lambda: {'database': {}})
    monkeypatch.setattr(operations, '_create_mssql_engine', lambda _cfg: engine)

    result = operations.lookup_shop_order('51034643')

    assert result is not None
    assert result['ShopOrder'] == '51034643'
    assert result['PartID'] == 'CERBERUS-575T-SEI'
    assert result['SequenceID'] == ''
    assert result['OrderQTY'] == 40
    assert result['OrderQty'] == 40
    assert result['AlternateBOM'] == 'BOM1'
    assert result['AlternateRouting'] == 'RTG1'
    assert engine.disposed is True


def test_lookup_shop_order_returns_none_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(operations, '_load_runtime_config', lambda: {'database': {}})
    monkeypatch.setattr(operations, '_create_mssql_engine', lambda _cfg: _FakeEngine(None))

    assert operations.lookup_shop_order('NOPE') is None


def test_validate_shop_order_custom_work_order_wins(monkeypatch) -> None:
    monkeypatch.setattr(
        operations,
        '_load_runtime_config',
        lambda: {
            'custom_work_orders': {
                'stinger228': {
                    'part_id': 'SPS00000',
                    'sequence_id': '300',
                    'order_qty': 1,
                },
            },
        },
    )

    def _unexpected_lookup(_shop_order):
        raise AssertionError('MAX lookup should not run for custom work orders')

    monkeypatch.setattr(operations, 'lookup_shop_order', _unexpected_lookup)

    result = operations.validate_shop_order('stinger228')

    assert result is not None
    assert result['PartID'] == 'SPS00000'
    assert result['SequenceID'] == '300'
    assert result['OrderQTY'] == 1
