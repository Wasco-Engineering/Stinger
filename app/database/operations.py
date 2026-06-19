"""
Database operations for Stinger.

Provides high-level functions for:
- Validating shop orders
- Loading test parameters
- Saving/updating test results
- Serial number management
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional, Set

import sqlalchemy
from sqlalchemy import create_engine, func, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError

from .models import OrderCalibrationMaster, ProductTestParameters, OrderCalibrationDetail
from .session import session_scope

logger = logging.getLogger(__name__)

ORDER_CAL_DETAIL_LIMITS: Dict[str, int] = {
    'shop_order': 10,
    'sequence_id': 4,
    'part_id': 30,
    'operator_id': 5,
    'equipment_id': 20,
    'units_of_measure': 20,
}


SHOP_ORDER_LOOKUP_SQL = text(
    """
    SELECT TOP 1
        ORDNUM_147,
        PRTNUM_147,
        CURQTY_147,
        CURDUE_147,
        STATUS_147,
        ALTBOM_147,
        ALTRTG_147
    FROM dbo.ShopOrder
    WHERE LTRIM(RTRIM(ORDNUM_147)) = :shop_order
    """
)


def _clean_string(value: Any) -> str:
    """Normalize string-ish database inputs before validation."""
    if value is None:
        return ''
    return str(value).strip()


def _validate_fixed_width(value: str, *, field_name: str, max_length: int) -> bool:
    """Reject values that exceed live SQL Server fixed-width column limits."""
    if len(value) <= max_length:
        return True
    logger.error(
        'Database write rejected: %s=%r exceeds max length %d',
        field_name,
        value,
        max_length,
    )
    return False


def _load_runtime_config() -> Dict[str, Any]:
    from app.core.config import load_config

    return load_config()


def _max_database_config(config: Dict[str, Any]) -> Dict[str, Any]:
    database_cfg = dict(config.get('database') or {})
    max_cfg = config.get('max_database') or config.get('shop_order_database') or {}
    if isinstance(max_cfg, dict):
        database_cfg.update(max_cfg)
    database_cfg['database'] = database_cfg.get('database') or 'ExactMAXWasco'
    if database_cfg['database'] == (config.get('database') or {}).get('database'):
        database_cfg['database'] = 'ExactMAXWasco'
    return database_cfg


def _create_mssql_engine(database_cfg: Dict[str, Any]):
    server = database_cfg.get('server', 'PASCAL')
    database = database_cfg.get('database', 'ExactMAXWasco')
    driver = database_cfg.get('driver', 'ODBC Driver 18 for SQL Server')
    timeout = int(float(database_cfg.get('connection_timeout_sec', 5)))
    username = database_cfg.get('username')
    password = database_cfg.get('password')

    query = {
        'driver': driver,
        'TrustServerCertificate': 'yes',
    }
    if username and password:
        connection_url = URL.create(
            'mssql+pyodbc',
            username=username,
            password=password,
            host=server,
            database=database,
            query=query,
        )
    else:
        query['Trusted_Connection'] = 'yes'
        connection_url = URL.create(
            'mssql+pyodbc',
            host=server,
            database=database,
            query=query,
        )

    return create_engine(
        connection_url,
        pool_pre_ping=True,
        connect_args={'timeout': timeout},
    )


def _parse_order_qty(value: Any) -> int:
    try:
        return max(int(float(value)), 0)
    except (TypeError, ValueError):
        return 0


def _sequence_id_lookup_values(sequence_id: str) -> list[str]:
    """Return DB sequence formats seen in legacy and current result rows."""
    raw = _clean_string(sequence_id)
    values = [raw] if raw else []
    try:
        sequence_number = int(raw)
    except (TypeError, ValueError):
        candidates = [raw]
    else:
        candidates = [str(sequence_number), f'{sequence_number:04d}']
    for candidate in candidates:
        if candidate and candidate not in values:
            values.append(candidate)
    return values


def is_calibration_database_available() -> bool:
    """Return whether WASCO_Calibration (PTP / results) can be reached."""
    try:
        config = _load_runtime_config()
        engine = _create_mssql_engine(dict(config.get('database') or {}))
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql('SELECT 1')
            return True
        finally:
            engine.dispose()
    except Exception as exc:
        logger.error('Calibration database availability check failed: %s', exc)
        return False


def is_shop_order_database_available() -> bool:
    """Return whether the MAX ShopOrder source can be reached."""
    try:
        engine = _create_mssql_engine(_max_database_config(_load_runtime_config()))
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql('SELECT 1')
            return True
        finally:
            engine.dispose()
    except Exception as exc:
        logger.error('ShopOrder database availability check failed: %s', exc)
        return False


def describe_database_connectivity() -> str:
    """Short operator-facing summary when both SQL sources fail."""
    config = _load_runtime_config()
    database_cfg = dict(config.get('database') or {})
    server = database_cfg.get('server', 'PASCAL')
    username = (database_cfg.get('username') or '').strip()
    auth = f'SQL login {username!r}' if username else 'Windows authentication'

    for label, cfg in (
        ('WASCO_Calibration', database_cfg),
        ('ExactMAXWasco', _max_database_config(config)),
    ):
        try:
            engine = _create_mssql_engine(cfg)
            try:
                with engine.connect() as conn:
                    conn.exec_driver_sql('SELECT 1')
            finally:
                engine.dispose()
        except Exception as exc:
            err = str(exc)
            if '18456' in err:
                return (
                    f'SQL login rejected on {server} ({auth}). '
                    'Check database.username and database.password in stinger_config.yaml.'
                )
            if '08001' in err or 'network' in err.lower():
                return f'Cannot reach SQL Server {server} ({label}): network or server name issue.'
            return f'Database error ({label}): {err[:160]}'

    return 'Database connection failed.'


def lookup_shop_order(shop_order: str) -> Optional[Dict[str, Any]]:
    """Look up a shop order in ExactMAXWasco.dbo.ShopOrder."""
    shop_order_clean = _clean_string(shop_order)
    if not shop_order_clean:
        return None

    engine = _create_mssql_engine(_max_database_config(_load_runtime_config()))
    try:
        with engine.connect() as conn:
            row = conn.execute(
                SHOP_ORDER_LOOKUP_SQL,
                {'shop_order': shop_order_clean},
            ).mappings().first()
        if not row:
            logger.warning('Shop order not found in MAX ShopOrder: %s', shop_order)
            return None

        order_qty = _parse_order_qty(row.get('CURQTY_147'))
        details = {
            'ShopOrder': _clean_string(row.get('ORDNUM_147')),
            'PartID': _clean_string(row.get('PRTNUM_147')),
            'SequenceID': '',
            'OrderQTY': order_qty,
            'OrderQty': order_qty,
            'DueDate': row.get('CURDUE_147'),
            'Status': _clean_string(row.get('STATUS_147')),
            'AlternateBOM': _clean_string(row.get('ALTBOM_147')),
            'AlternateRouting': _clean_string(row.get('ALTRTG_147')),
            'OperatorID': None,
            'EquipmentID': None,
        }
        logger.info(
            'Shop order validated from MAX: %s -> %s qty=%s',
            details['ShopOrder'],
            details['PartID'],
            details['OrderQTY'],
        )
        return details
    finally:
        engine.dispose()


def validate_shop_order(shop_order: str) -> Optional[Dict[str, Any]]:
    """
    Validate a shop order and return work order details.

    Checks custom_work_orders from config first; if not found, queries MAX ShopOrder.

    Args:
        shop_order: The shop order number to validate.

    Returns:
        Dictionary with work order details, or None if not found.
    """
    if not shop_order:
        return None

    shop_order_clean = shop_order.strip()

    # Check config-based custom work orders (e.g. stinger228)
    try:
        config = _load_runtime_config()
        custom = config.get("custom_work_orders") or {}
        if isinstance(custom, dict) and shop_order_clean in custom:
            c = custom[shop_order_clean]
            if isinstance(c, dict):
                part_id = str(c.get("part_id", "")).strip()
                sequence_id = str(c.get("sequence_id", "")).strip()
                order_qty = c.get("order_qty", 1)
                if part_id and sequence_id:
                    details = {
                        "ShopOrder": shop_order_clean,
                        "PartID": part_id,
                        "SequenceID": sequence_id,
                        "OrderQTY": order_qty,
                        "OrderQty": order_qty,
                        "OperatorID": None,
                        "EquipmentID": None,
                    }
                    logger.info(f"Custom work order validated: {shop_order} -> {part_id}/{sequence_id}")
                    return details
    except Exception as e:
        logger.debug("Custom work order check failed: %s", e)

    try:
        return lookup_shop_order(shop_order_clean)
    except SQLAlchemyError as e:
        logger.error(f"Database error validating shop order: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error validating shop order: {e}")
        return None


def load_test_parameters(part_id: str, sequence_id: str) -> Dict[str, str]:
    """
    Load test parameters for a part/sequence combination.
    
    Args:
        part_id: Part ID from work order.
        sequence_id: Sequence ID from work order.
        
    Returns:
        Dictionary mapping ParameterName -> ParameterValue.
    """
    if not part_id or not sequence_id:
        return {}
    
    try:
        with session_scope() as session:
            # Normalize sequence ID (may be stored with or without zero-padding)
            seq_normalized = str(int(sequence_id.strip()))
            
            # Query PTP
            results = session.query(ProductTestParameters).filter(
                ProductTestParameters.PartID == part_id.strip(),
                func.cast(func.rtrim(ProductTestParameters.SequenceID), sqlalchemy.Integer)
                == int(seq_normalized),
            ).all()
            
            # Convert to dictionary, stripping padding
            params = {}
            for row in results:
                name = row.ParameterName.strip() if row.ParameterName else None
                value = row.ParameterValue.strip() if row.ParameterValue else None
                if name:
                    params[name] = value
            
            logger.info(f"Loaded {len(params)} PTP parameters for {part_id}/{sequence_id}")
            return params
            
    except SQLAlchemyError as e:
        logger.error(f"Database error loading PTP: {e}")
        return {}
    except Exception as e:
        logger.error(f"Unexpected error loading PTP: {e}")
        return {}



def insert_test_parameters(part_id: str, sequence_id: str, params: Dict[str, str]) -> bool:
    """
    Insert or replace PTP parameters for a part/sequence.

    Deletes any existing rows for (part_id, sequence_id), then inserts the new params.
    Returns True on success.

    Args:
        part_id: Part ID.
        sequence_id: Sequence ID.
        params: Dictionary mapping ParameterName -> ParameterValue (strings).

    Returns:
        True if successful, False on error.
    """
    if not part_id or not sequence_id or not params:
        logger.warning("insert_test_parameters: part_id, sequence_id, and params required")
        return False

    try:
        part_id_clean = part_id.strip()
        seq_normalized = str(int(sequence_id.strip()))

        with session_scope() as session:
            # Delete existing rows for this part/sequence
            deleted = session.query(ProductTestParameters).filter(
                ProductTestParameters.PartID == part_id_clean,
                func.cast(func.rtrim(ProductTestParameters.SequenceID), sqlalchemy.Integer)
                == int(seq_normalized),
            ).delete(synchronize_session=False)

            if deleted:
                logger.info(f"Deleted {deleted} existing PTP rows for {part_id_clean}/{seq_normalized}")

            # Insert new rows
            for param_name, param_value in params.items():
                if not param_name or param_name.strip() == "":
                    continue
                value_str = str(param_value).strip() if param_value is not None else ""
                record = ProductTestParameters(
                    PartID=part_id_clean,
                    SequenceID=seq_normalized,
                    ParameterName=param_name.strip(),
                    ParameterValue=value_str,
                )
                session.add(record)

        logger.info(f"Inserted {len(params)} PTP parameters for {part_id_clean}/{seq_normalized}")
        return True

    except SQLAlchemyError as e:
        logger.error(f"Database error inserting PTP: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error inserting PTP: {e}")
        return False


def insert_work_order_master(
    shop_order: str,
    part_id: str,
    sequence_id: str,
    order_qty: int = 1,
    activation_target: Optional[float] = None,
) -> bool:
    """
    Insert or update a work order in OrderCalibrationMaster.

    If the shop order exists, updates PartID, LastSequenceCalibrated, OrderQTY.
    Otherwise inserts a new row, using an existing row as template for required columns.

    Args:
        shop_order: Shop order number.
        part_id: Part ID.
        sequence_id: Sequence ID (e.g. "300").
        order_qty: Order quantity.
        activation_target: Optional activation target (e.g. 22.8 for 22.8 psi).

    Returns:
        True if successful, False on error.
    """
    if not shop_order or not part_id or not sequence_id:
        logger.warning("insert_work_order_master: shop_order, part_id, sequence_id required")
        return False

    try:
        shop_order_clean = shop_order.strip()
        part_id_clean = part_id.strip()
        seq_normalized = str(int(sequence_id.strip()))
        target = float(activation_target) if activation_target is not None else 22.8

        with session_scope() as session:
            existing = session.query(OrderCalibrationMaster).filter_by(
                ShopOrder=shop_order_clean
            ).first()

            if existing:
                existing.PartID = part_id_clean
                existing.LastSequenceCalibrated = seq_normalized
                existing.OrderQTY = order_qty
                existing.ActivationTarget = target
                logger.info(f"Updated work order {shop_order_clean} -> {part_id_clean}/{seq_normalized}")
            else:
                # Use an existing row as template (table has many NOT NULL columns)
                template = (
                    session.query(OrderCalibrationMaster)
                    .filter(OrderCalibrationMaster.PartID.isnot(None))
                    .first()
                )
                if not template:
                    logger.error("No existing OrderCalibrationMaster row to use as template")
                    return False

                now = datetime.now()
                # Copy all columns from template, override key fields
                record = OrderCalibrationMaster(
                    ShopOrder=shop_order_clean,
                    PartID=part_id_clean,
                    LastSequenceCalibrated=seq_normalized,
                    OrderQTY=order_qty,
                    OperatorID=template.OperatorID or "Sys",
                    EquipmentID=template.EquipmentID or "Sys",
                    StartTime=now,
                    FinishTime=now,
                    CalibrationDate=now,
                    ModificationDate=now,
                    TemperatureC=template.TemperatureC if template.TemperatureC is not None else 20.0,
                    ActivationTarget=target,
                )
                # Copy activation limits from template (or use defaults around target)
                record.ActivationMaxAllowable = (
                    template.ActivationMaxAllowable
                    if template.ActivationMaxAllowable is not None
                    else target + 1.0
                )
                record.ActivationMinAllowable = (
                    template.ActivationMinAllowable
                    if template.ActivationMinAllowable is not None
                    else target - 1.0
                )
                session.add(record)
                logger.info(f"Inserted work order {shop_order_clean} -> {part_id_clean}/{seq_normalized}")

        return True

    except SQLAlchemyError as e:
        logger.error(f"Database error inserting work order: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error inserting work order: {e}")
        return False


def get_tested_serials(shop_order: str, part_id: str, sequence_id: str) -> Set[int]:
    """
    Get set of serial numbers already tested for a work order.
    
    Args:
        shop_order: Shop order number.
        part_id: Part ID.
        sequence_id: Sequence ID.
        
    Returns:
        Set of serial numbers that have been tested.
    """
    try:
        with session_scope() as session:
            sequence_values = _sequence_id_lookup_values(sequence_id)
            
            results = session.query(OrderCalibrationDetail.SerialNumber).filter(
                OrderCalibrationDetail.ShopOrder == shop_order.strip(),
                OrderCalibrationDetail.PartID == part_id.strip(),
                OrderCalibrationDetail.SequenceID.in_(sequence_values),
            ).distinct().all()
            
            return {row[0] for row in results}
            
    except SQLAlchemyError as e:
        logger.error(f"Database error getting tested serials: {e}")
        return set()


def get_next_serial_number(
    shop_order: str, 
    part_id: str, 
    sequence_id: str,
    in_progress_serials: Set[int] = None,
    start_from: int = 1
) -> int:
    """
    Get the next available serial number for a work order.
    
    Args:
        shop_order: Shop order number.
        part_id: Part ID.
        sequence_id: Sequence ID.
        in_progress_serials: Set of serials currently being tested on other ports.
        start_from: Minimum serial number to consider.
        
    Returns:
        Next available serial number.
    """
    if in_progress_serials is None:
        in_progress_serials = set()
    
    # Get already-tested serials from database
    tested = get_tested_serials(shop_order, part_id, sequence_id)
    
    # Find next available
    serial = start_from
    while serial in tested or serial in in_progress_serials:
        serial += 1
    
    return serial


def save_test_result(
    shop_order: str,
    part_id: str,
    sequence_id: str,
    serial_number: int,
    increasing_activation: Optional[float],
    decreasing_deactivation: Optional[float],
    in_spec: bool,
    temperature_c: float,
    units_of_measure: str,
    operator_id: str,
    equipment_id: str,
    activation_id: int = 1
) -> bool:
    """
    Save or update a test result.
    
    Implements UPDATE behavior for retests - updates existing row if present.
    
    Args:
        shop_order: Shop order number.
        part_id: Part ID.
        sequence_id: Sequence ID.
        serial_number: Unit serial number.
        increasing_activation: Measured activation pressure (increasing direction).
        decreasing_deactivation: Measured deactivation pressure (decreasing direction).
        in_spec: True if passed, False if failed.
        temperature_c: Ambient test temperature in Celsius.
        units_of_measure: Units string for display.
        operator_id: Operator who performed the test.
        equipment_id: Equipment identifier.
        activation_id: Attempt identifier (usually 1).
        
    Returns:
        True if save successful.
    """
    action = 'Saved'
    try:
        shop_order_clean = _clean_string(shop_order)
        part_id_clean = _clean_string(part_id)
        operator_id_clean = _clean_string(operator_id)
        equipment_id_clean = _clean_string(equipment_id)
        units_clean = _clean_string(units_of_measure) or 'PSI'
        seq_formatted = f"{int(str(sequence_id).strip()):04d}"

        if not _validate_fixed_width(
            shop_order_clean,
            field_name='shop_order',
            max_length=ORDER_CAL_DETAIL_LIMITS['shop_order'],
        ):
            return False
        if not _validate_fixed_width(
            seq_formatted,
            field_name='sequence_id',
            max_length=ORDER_CAL_DETAIL_LIMITS['sequence_id'],
        ):
            return False
        if not _validate_fixed_width(
            part_id_clean,
            field_name='part_id',
            max_length=ORDER_CAL_DETAIL_LIMITS['part_id'],
        ):
            return False
        if not _validate_fixed_width(
            operator_id_clean,
            field_name='operator_id',
            max_length=ORDER_CAL_DETAIL_LIMITS['operator_id'],
        ):
            return False
        if not _validate_fixed_width(
            equipment_id_clean,
            field_name='equipment_id',
            max_length=ORDER_CAL_DETAIL_LIMITS['equipment_id'],
        ):
            return False
        if not _validate_fixed_width(
            units_clean,
            field_name='units_of_measure',
            max_length=ORDER_CAL_DETAIL_LIMITS['units_of_measure'],
        ):
            return False

        with session_scope() as session:
            # Check if record exists
            existing = session.query(OrderCalibrationDetail).filter_by(
                ShopOrder=shop_order_clean,
                SequenceID=seq_formatted,
                PartID=part_id_clean,
                SerialNumber=serial_number,
                ActivationID=activation_id
            ).one_or_none()
            
            if existing:
                # Update existing record
                existing.IncreasingActivation = increasing_activation
                existing.DecreasingDeactivation = decreasing_deactivation
                existing.TemperatureC = temperature_c
                existing.IncreasingGap = 0
                existing.DecreasingGap = 0
                existing.InSpec = in_spec
                existing.UnitsOfMeasure = units_clean
                existing.InspectionDate = datetime.now()
                existing.OperatorID = operator_id_clean
                existing.EquipmentID = equipment_id_clean
                action = 'Updated'
            else:
                # Insert new record
                record = OrderCalibrationDetail(
                    ShopOrder=shop_order_clean,
                    SequenceID=seq_formatted,
                    PartID=part_id_clean,
                    SerialNumber=serial_number,
                    ActivationID=activation_id,
                    IncreasingActivation=increasing_activation,
                    DecreasingDeactivation=decreasing_deactivation,
                    TemperatureC=temperature_c,
                    IncreasingGap=0,
                    DecreasingGap=0,
                    InSpec=in_spec,
                    UnitsOfMeasure=units_clean,
                    InspectionDate=datetime.now(),
                    OperatorID=operator_id_clean,
                    EquipmentID=equipment_id_clean
                )
                session.add(record)
                action = 'Inserted'

        logger.info(f"{action} test result: SN={serial_number}, InSpec={in_spec}")
        return True
            
    except SQLAlchemyError as e:
        logger.error(f"Database error saving test result: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error saving test result: {e}")
        return False


def get_work_order_progress(shop_order: str, part_id: str, sequence_id: str) -> Dict[str, int]:
    """
    Get progress for a work order.
    
    Args:
        shop_order: Shop order number.
        part_id: Part ID.
        sequence_id: Sequence ID.
        
    Returns:
        Dictionary with 'completed', 'passed', 'failed' counts.
    """
    try:
        with session_scope() as session:
            sequence_values = _sequence_id_lookup_values(sequence_id)
            shop_order_clean = shop_order.strip()
            part_id_clean = part_id.strip()
            
            # Use window function to get the latest ActivationID per serial number
            # This is much more efficient than fetching all rows and grouping in Python
            # Subquery: get max ActivationID for each SerialNumber
            latest_per_serial = session.query(
                OrderCalibrationDetail.SerialNumber,
                func.max(OrderCalibrationDetail.ActivationID).label('max_activation_id')
            ).filter(
                OrderCalibrationDetail.ShopOrder == shop_order_clean,
                OrderCalibrationDetail.PartID == part_id_clean,
                OrderCalibrationDetail.SequenceID.in_(sequence_values),
            ).group_by(
                OrderCalibrationDetail.SerialNumber
            ).subquery()
            
            # Join to get the InSpec status for the latest attempt of each serial
            latest_results = session.query(
                OrderCalibrationDetail.InSpec
            ).join(
                latest_per_serial,
                (OrderCalibrationDetail.SerialNumber == latest_per_serial.c.SerialNumber) &
                (OrderCalibrationDetail.ActivationID == latest_per_serial.c.max_activation_id)
            ).filter(
                OrderCalibrationDetail.ShopOrder == shop_order_clean,
                OrderCalibrationDetail.PartID == part_id_clean,
                OrderCalibrationDetail.SequenceID.in_(sequence_values),
            ).all()
            
            # Count results
            completed = len(latest_results)
            passed = sum(1 for r in latest_results if r.InSpec)
            failed = completed - passed
            
            return {
                'completed': completed,
                'passed': passed,
                'failed': failed,
            }
            
    except SQLAlchemyError as e:
        logger.error(f"Database error getting progress: {e}")
        return {'completed': 0, 'passed': 0, 'failed': 0}
