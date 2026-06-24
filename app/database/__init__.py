"""
Database layer for Stinger.

Provides access to the SQL Server calibration database:
- OrderCalibrationMaster (work order context)
- ProductTestParameters (test parameters)
- OrderCalibrationDetail (test results)
"""

from .models import OrderCalibrationMaster, ProductTestParameters, OrderCalibrationDetail
from .session import get_db_session, session_scope
from .operations import (
    validate_shop_order,
    load_test_parameters,
    insert_test_parameters,
    ensure_work_order_master,
    insert_work_order_master,
    save_test_result,
    get_next_serial_number,
    get_local_queue_count,
    sync_local_cache,
)

__all__ = [
    'OrderCalibrationMaster',
    'ProductTestParameters', 
    'OrderCalibrationDetail',
    'get_db_session',
    'session_scope',
    'validate_shop_order',
    'load_test_parameters',
    'insert_test_parameters',
    'ensure_work_order_master',
    'insert_work_order_master',
    'save_test_result',
    'get_next_serial_number',
    'get_local_queue_count',
    'sync_local_cache',
]
