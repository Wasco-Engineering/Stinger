"""Machine-local SQLite cache for offline Stinger database work."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, Optional

from app.core.paths import get_config_dir

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_CACHE_FILENAME = 'stinger_local.sqlite3'
SYNC_QUEUED = 'queued'
SYNC_SYNCED = 'synced'
SYNC_CONFLICT = 'conflict'
SYNC_ERROR = 'error'

DETAIL_KEY_FIELDS = (
    'ShopOrder',
    'SequenceID',
    'PartID',
    'SerialNumber',
    'ActivationID',
)


def _load_runtime_config() -> Dict[str, Any]:
    try:
        from app.core.config import load_config

        return load_config()
    except Exception:
        return {}


def local_cache_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return normalized local-cache config defaults."""
    resolved_config = config if isinstance(config, dict) else _load_runtime_config()
    database_cfg = resolved_config.get('database') if isinstance(resolved_config, dict) else {}
    cache_cfg = database_cfg.get('local_cache') if isinstance(database_cfg, dict) else {}
    if not isinstance(cache_cfg, dict):
        cache_cfg = {}
    return {
        'enabled': bool(cache_cfg.get('enabled', True)),
        'path': cache_cfg.get('path') or DEFAULT_LOCAL_CACHE_FILENAME,
        'sync_interval_sec': int(float(cache_cfg.get('sync_interval_sec', 60))),
    }


def is_enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    return bool(local_cache_config(config)['enabled'])


def get_local_cache_path(config: Optional[Dict[str, Any]] = None) -> Path:
    """Resolve the local SQLite cache path under the stand config directory."""
    raw_path = Path(str(local_cache_config(config)['path'])).expanduser()
    if raw_path.is_absolute():
        return raw_path
    return get_config_dir() / raw_path


@contextmanager
def connect(config: Optional[Dict[str, Any]] = None) -> Generator[sqlite3.Connection, None, None]:
    """Open a local cache connection and ensure the schema exists."""
    path = get_local_cache_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        initialize_local_cache(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_local_cache(conn: Optional[sqlite3.Connection] = None) -> None:
    """Create or migrate the local cache schema."""
    close_after = False
    if conn is None:
        path = get_local_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        close_after = True

    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS order_calibration_master (
                ShopOrder TEXT PRIMARY KEY,
                PartID TEXT NOT NULL,
                WascoDescription TEXT,
                LastSequenceCalibrated TEXT,
                OrderQTY INTEGER NOT NULL DEFAULT 0,
                OperatorID TEXT,
                EquipmentID TEXT,
                StartTime TEXT,
                FinishTime TEXT,
                CalibrationDate TEXT,
                ModificationDate TEXT,
                TemperatureC REAL,
                ActivationTarget TEXT,
                ActivationMaxAllowable REAL,
                ActivationMinAllowable REAL,
                CreatedBy TEXT,
                CreationDate TEXT,
                ModifiedBy TEXT,
                source TEXT NOT NULL DEFAULT 'local',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS product_test_parameters (
                PartID TEXT NOT NULL,
                SequenceID TEXT NOT NULL,
                ParameterName TEXT NOT NULL,
                ParameterValue TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'local',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (PartID, SequenceID, ParameterName)
            );

            CREATE TABLE IF NOT EXISTS order_calibration_detail (
                ShopOrder TEXT NOT NULL,
                SequenceID TEXT NOT NULL,
                PartID TEXT NOT NULL,
                SerialNumber INTEGER NOT NULL,
                ActivationID INTEGER NOT NULL DEFAULT 1,
                InspectionDate TEXT NOT NULL,
                TemperatureC REAL NOT NULL DEFAULT 0,
                EquipmentID TEXT,
                OperatorID TEXT,
                IncreasingActivation REAL NOT NULL DEFAULT 0,
                DecreasingDeactivation REAL NOT NULL DEFAULT 0,
                IncreasingGap REAL NOT NULL DEFAULT 0,
                DecreasingGap REAL NOT NULL DEFAULT 0,
                MaxPressureAchieved REAL,
                UnitsOfMeasure TEXT,
                GageReferenceDiff REAL,
                InSpec INTEGER,
                sync_status TEXT NOT NULL DEFAULT 'queued',
                sync_error TEXT,
                updated_at TEXT NOT NULL,
                synced_at TEXT,
                PRIMARY KEY (ShopOrder, SequenceID, PartID, SerialNumber, ActivationID)
            );

            CREATE INDEX IF NOT EXISTS idx_local_detail_progress
                ON order_calibration_detail (ShopOrder, PartID, SequenceID, SerialNumber, ActivationID);
            CREATE INDEX IF NOT EXISTS idx_local_detail_sync
                ON order_calibration_detail (sync_status, updated_at);
            """
        )
        conn.commit()
    finally:
        if close_after:
            conn.close()


def _clean(value: Any) -> str:
    return '' if value is None else str(value).strip()


def _as_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=' ', timespec='seconds')
    return str(value)


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _now_iso() -> str:
    return datetime.now().isoformat(sep=' ', timespec='seconds')


def sequence_lookup_values(sequence_id: str) -> list[str]:
    raw = _clean(sequence_id)
    values = [raw] if raw else []
    try:
        number = int(raw)
    except (TypeError, ValueError):
        candidates = [raw]
    else:
        candidates = [str(number), f'{number:04d}']
    for candidate in candidates:
        if candidate and candidate not in values:
            values.append(candidate)
    return values


def upsert_master(details: Dict[str, Any], *, source: str = 'local') -> None:
    """Insert/update a local master row."""
    shop_order = _clean(details.get('ShopOrder') or details.get('shop_order'))
    part_id = _clean(details.get('PartID') or details.get('part_id'))
    if not shop_order or not part_id:
        return

    now = _now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO order_calibration_master (
                ShopOrder, PartID, WascoDescription, LastSequenceCalibrated, OrderQTY,
                OperatorID, EquipmentID, StartTime, FinishTime, CalibrationDate,
                ModificationDate, TemperatureC, ActivationTarget,
                ActivationMaxAllowable, ActivationMinAllowable, CreatedBy, CreationDate,
                ModifiedBy, source, updated_at
            )
            VALUES (
                :ShopOrder, :PartID, :WascoDescription, :LastSequenceCalibrated, :OrderQTY,
                :OperatorID, :EquipmentID, :StartTime, :FinishTime, :CalibrationDate,
                :ModificationDate, :TemperatureC, :ActivationTarget,
                :ActivationMaxAllowable, :ActivationMinAllowable, :CreatedBy, :CreationDate,
                :ModifiedBy, :source, :updated_at
            )
            ON CONFLICT(ShopOrder) DO UPDATE SET
                PartID=excluded.PartID,
                WascoDescription=excluded.WascoDescription,
                LastSequenceCalibrated=excluded.LastSequenceCalibrated,
                OrderQTY=excluded.OrderQTY,
                OperatorID=excluded.OperatorID,
                EquipmentID=excluded.EquipmentID,
                StartTime=excluded.StartTime,
                FinishTime=excluded.FinishTime,
                CalibrationDate=excluded.CalibrationDate,
                ModificationDate=excluded.ModificationDate,
                TemperatureC=excluded.TemperatureC,
                ActivationTarget=excluded.ActivationTarget,
                ActivationMaxAllowable=excluded.ActivationMaxAllowable,
                ActivationMinAllowable=excluded.ActivationMinAllowable,
                CreatedBy=excluded.CreatedBy,
                CreationDate=excluded.CreationDate,
                ModifiedBy=excluded.ModifiedBy,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            {
                'ShopOrder': shop_order,
                'PartID': part_id,
                'WascoDescription': _clean(details.get('WascoDescription')),
                'LastSequenceCalibrated': _clean(
                    details.get('LastSequenceCalibrated') or details.get('SequenceID')
                ),
                'OrderQTY': _as_int(details.get('OrderQTY') or details.get('OrderQty'), 0),
                'OperatorID': _clean(details.get('OperatorID')),
                'EquipmentID': _clean(details.get('EquipmentID')),
                'StartTime': _as_iso(details.get('StartTime')),
                'FinishTime': _as_iso(details.get('FinishTime')),
                'CalibrationDate': _as_iso(details.get('CalibrationDate')),
                'ModificationDate': _as_iso(details.get('ModificationDate')),
                'TemperatureC': _as_float(details.get('TemperatureC')),
                'ActivationTarget': _clean(details.get('ActivationTarget')),
                'ActivationMaxAllowable': _as_float(details.get('ActivationMaxAllowable')),
                'ActivationMinAllowable': _as_float(details.get('ActivationMinAllowable')),
                'CreatedBy': _clean(details.get('CreatedBy')),
                'CreationDate': _as_iso(details.get('CreationDate')),
                'ModifiedBy': _clean(details.get('ModifiedBy')),
                'source': source,
                'updated_at': now,
            },
        )


def get_master(shop_order: str) -> Optional[Dict[str, Any]]:
    shop_order_clean = _clean(shop_order)
    if not shop_order_clean:
        return None
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM order_calibration_master
            WHERE ShopOrder = ?
            """,
            (shop_order_clean,),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result['SequenceID'] = result.get('LastSequenceCalibrated') or ''
    result['OrderQty'] = result.get('OrderQTY') or 0
    return result


def upsert_ptp(part_id: str, sequence_id: str, params: Dict[str, Any], *, source: str = 'sql') -> None:
    """Replace local PTP rows for a part/sequence."""
    part_id_clean = _clean(part_id)
    sequence_clean = _clean(sequence_id)
    if not part_id_clean or not sequence_clean or not params:
        return
    now = _now_iso()
    with connect() as conn:
        placeholders = ','.join('?' for _ in sequence_lookup_values(sequence_clean))
        conn.execute(
            f"""
            DELETE FROM product_test_parameters
            WHERE PartID = ? AND SequenceID IN ({placeholders})
            """,
            (part_id_clean, *sequence_lookup_values(sequence_clean)),
        )
        conn.executemany(
            """
            INSERT INTO product_test_parameters (
                PartID, SequenceID, ParameterName, ParameterValue, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    part_id_clean,
                    sequence_clean,
                    _clean(name),
                    _clean(value),
                    source,
                    now,
                )
                for name, value in params.items()
                if _clean(name)
            ],
        )


def load_ptp(part_id: str, sequence_id: str) -> Dict[str, str]:
    part_id_clean = _clean(part_id)
    sequence_values = sequence_lookup_values(sequence_id)
    if not part_id_clean or not sequence_values:
        return {}
    placeholders = ','.join('?' for _ in sequence_values)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT ParameterName, ParameterValue
            FROM product_test_parameters
            WHERE PartID = ? AND SequenceID IN ({placeholders})
            """,
            (part_id_clean, *sequence_values),
        ).fetchall()
    return {
        _clean(row['ParameterName']): _clean(row['ParameterValue'])
        for row in rows
        if _clean(row['ParameterName'])
    }


def detail_key(detail: Dict[str, Any]) -> tuple[Any, ...]:
    return tuple(detail.get(field) for field in DETAIL_KEY_FIELDS)


def upsert_detail(detail: Dict[str, Any], *, sync_status: str = SYNC_QUEUED) -> None:
    """Insert/update a local detail row."""
    normalized = normalize_detail(detail)
    now = _now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO order_calibration_detail (
                ShopOrder, SequenceID, PartID, SerialNumber, ActivationID,
                InspectionDate, TemperatureC, EquipmentID, OperatorID,
                IncreasingActivation, DecreasingDeactivation, IncreasingGap,
                DecreasingGap, MaxPressureAchieved, UnitsOfMeasure,
                GageReferenceDiff, InSpec, sync_status, sync_error, updated_at,
                synced_at
            )
            VALUES (
                :ShopOrder, :SequenceID, :PartID, :SerialNumber, :ActivationID,
                :InspectionDate, :TemperatureC, :EquipmentID, :OperatorID,
                :IncreasingActivation, :DecreasingDeactivation, :IncreasingGap,
                :DecreasingGap, :MaxPressureAchieved, :UnitsOfMeasure,
                :GageReferenceDiff, :InSpec, :sync_status, :sync_error, :updated_at,
                :synced_at
            )
            ON CONFLICT(ShopOrder, SequenceID, PartID, SerialNumber, ActivationID)
            DO UPDATE SET
                InspectionDate=excluded.InspectionDate,
                TemperatureC=excluded.TemperatureC,
                EquipmentID=excluded.EquipmentID,
                OperatorID=excluded.OperatorID,
                IncreasingActivation=excluded.IncreasingActivation,
                DecreasingDeactivation=excluded.DecreasingDeactivation,
                IncreasingGap=excluded.IncreasingGap,
                DecreasingGap=excluded.DecreasingGap,
                MaxPressureAchieved=excluded.MaxPressureAchieved,
                UnitsOfMeasure=excluded.UnitsOfMeasure,
                GageReferenceDiff=excluded.GageReferenceDiff,
                InSpec=excluded.InSpec,
                sync_status=excluded.sync_status,
                sync_error=excluded.sync_error,
                updated_at=excluded.updated_at,
                synced_at=excluded.synced_at
            """,
            {
                **normalized,
                'sync_status': sync_status,
                'sync_error': None,
                'updated_at': now,
                'synced_at': now if sync_status == SYNC_SYNCED else None,
            },
        )


def normalize_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'ShopOrder': _clean(_first_present(detail.get('ShopOrder'), detail.get('shop_order'))),
        'SequenceID': _clean(_first_present(detail.get('SequenceID'), detail.get('sequence_id'))),
        'PartID': _clean(_first_present(detail.get('PartID'), detail.get('part_id'))),
        'SerialNumber': _as_int(_first_present(detail.get('SerialNumber'), detail.get('serial_number')), 0),
        'ActivationID': _as_int(_first_present(detail.get('ActivationID'), detail.get('activation_id')), 1),
        'InspectionDate': _as_iso(detail.get('InspectionDate')) or _now_iso(),
        'TemperatureC': _as_float(
            _first_present(detail.get('TemperatureC'), detail.get('temperature_c'))
        ) or 0.0,
        'EquipmentID': _clean(_first_present(detail.get('EquipmentID'), detail.get('equipment_id'))),
        'OperatorID': _clean(_first_present(detail.get('OperatorID'), detail.get('operator_id'))),
        'IncreasingActivation': (
            _as_float(_first_present(detail.get('IncreasingActivation'), detail.get('increasing_activation')))
            or 0.0
        ),
        'DecreasingDeactivation': (
            _as_float(
                _first_present(
                    detail.get('DecreasingDeactivation'),
                    detail.get('decreasing_deactivation'),
                )
            )
            or 0.0
        ),
        'IncreasingGap': _as_float(
            _first_present(detail.get('IncreasingGap'), detail.get('increasing_gap'))
        ) or 0.0,
        'DecreasingGap': _as_float(
            _first_present(detail.get('DecreasingGap'), detail.get('decreasing_gap'))
        ) or 0.0,
        'MaxPressureAchieved': _as_float(
            _first_present(detail.get('MaxPressureAchieved'), detail.get('max_pressure_achieved'))
        ),
        'UnitsOfMeasure': _clean(
            _first_present(detail.get('UnitsOfMeasure'), detail.get('units_of_measure'))
        ) or 'PSI',
        'GageReferenceDiff': _as_float(
            _first_present(detail.get('GageReferenceDiff'), detail.get('gage_reference_diff'))
        ),
        'InSpec': None if detail.get('InSpec') is None and detail.get('in_spec') is None else int(
            bool(detail.get('InSpec') if detail.get('InSpec') is not None else detail.get('in_spec'))
        ),
    }


def get_tested_serials(shop_order: str, part_id: str, sequence_id: str) -> set[int]:
    sequence_values = sequence_lookup_values(sequence_id)
    if not sequence_values:
        return set()
    placeholders = ','.join('?' for _ in sequence_values)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT SerialNumber
            FROM order_calibration_detail
            WHERE ShopOrder = ? AND PartID = ? AND SequenceID IN ({placeholders})
            """,
            (_clean(shop_order), _clean(part_id), *sequence_values),
        ).fetchall()
    return {_as_int(row['SerialNumber']) for row in rows}


def get_progress(shop_order: str, part_id: str, sequence_id: str) -> Dict[str, int]:
    sequence_values = sequence_lookup_values(sequence_id)
    if not sequence_values:
        return {'completed': 0, 'passed': 0, 'failed': 0}
    placeholders = ','.join('?' for _ in sequence_values)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT d.InSpec
            FROM order_calibration_detail d
            JOIN (
                SELECT SerialNumber, MAX(ActivationID) AS max_activation_id
                FROM order_calibration_detail
                WHERE ShopOrder = ? AND PartID = ? AND SequenceID IN ({placeholders})
                GROUP BY SerialNumber
            ) latest
              ON d.SerialNumber = latest.SerialNumber
             AND d.ActivationID = latest.max_activation_id
            WHERE d.ShopOrder = ? AND d.PartID = ? AND d.SequenceID IN ({placeholders})
            """,
            (
                _clean(shop_order),
                _clean(part_id),
                *sequence_values,
                _clean(shop_order),
                _clean(part_id),
                *sequence_values,
            ),
        ).fetchall()
    completed = len(rows)
    passed = sum(1 for row in rows if bool(row['InSpec']))
    return {'completed': completed, 'passed': passed, 'failed': completed - passed}


def queued_count() -> int:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM order_calibration_detail
            WHERE sync_status IN (?, ?)
            """,
            (SYNC_QUEUED, SYNC_ERROR),
        ).fetchone()
    return int(row['cnt'] if row else 0)


def list_queued_details(limit: int = 100) -> list[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM order_calibration_detail
            WHERE sync_status IN (?, ?)
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (SYNC_QUEUED, SYNC_ERROR, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_detail_status(detail: Dict[str, Any], status: str, error: Optional[str] = None) -> None:
    key = tuple(detail.get(field) for field in DETAIL_KEY_FIELDS)
    with connect() as conn:
        conn.execute(
            """
            UPDATE order_calibration_detail
            SET sync_status = ?,
                sync_error = ?,
                synced_at = CASE WHEN ? = ? THEN ? ELSE synced_at END,
                updated_at = ?
            WHERE ShopOrder = ?
              AND SequenceID = ?
              AND PartID = ?
              AND SerialNumber = ?
              AND ActivationID = ?
            """,
            (
                status,
                error,
                status,
                SYNC_SYNCED,
                _now_iso(),
                _now_iso(),
                *key,
            ),
        )


def iter_rows(conn: sqlite3.Connection, table: str) -> Iterable[sqlite3.Row]:
    return conn.execute(f'SELECT * FROM {table}').fetchall()
