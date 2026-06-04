#!/usr/bin/env python3
"""Test SQL Server connectivity using stinger_config.yaml (same path logic as the app)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pyodbc
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from app.core.config import get_default_config_path, load_config
from app.database import operations as db_ops
from app.database.session import initialize_database


def _mask(s: str) -> str:
    if len(s) <= 2:
        return '***'
    return s[:1] + '*' * (len(s) - 2) + s[-1:]


def main() -> int:
    cfg_path = get_default_config_path()
    print(f'Config file: {cfg_path}')
    if not cfg_path.is_file():
        print('ERROR: config file missing')
        return 1

    config = load_config()
    dbc = dict(config.get('database') or {})
    max_dbc = db_ops._max_database_config(config)

    server = dbc.get('server', 'PASCAL')
    driver = dbc.get('driver', 'ODBC Driver 18 for SQL Server')
    username = (dbc.get('username') or '').strip()
    password = dbc.get('password') or ''

    print(f'Server: {server}')
    print(f'Driver: {driver}')
    print(f'Auth: {"SQL (" + username + ")" if username and password else "Windows (Trusted_Connection)"}')
    if password:
        print(f'Password: {_mask(str(password))} (len={len(str(password))})')

    drivers = [d for d in pyodbc.drivers() if 'SQL Server' in d]
    if driver not in drivers:
        print(f'WARNING: configured driver not installed. Installed: {drivers}')

    def pyodbc_test(label: str, database: str, use_sql: bool) -> bool:
        parts = [
            f'DRIVER={{{driver}}}',
            f'SERVER={server}',
            f'DATABASE={database}',
            'TrustServerCertificate=yes',
        ]
        if use_sql and username and password:
            parts.append(f'UID={username}')
            parts.append(f'PWD={password}')
        else:
            parts.append('Trusted_Connection=yes')
        conn_str = ';'.join(parts) + ';'
        try:
            conn = pyodbc.connect(conn_str, timeout=int(float(dbc.get('connection_timeout_sec', 5))))
            cur = conn.cursor()
            cur.execute('SELECT DB_NAME(), SYSTEM_USER')
            row = cur.fetchone()
            conn.close()
            print(f'  OK  {label}: db={row[0]!r} user={row[1]!r}')
            return True
        except pyodbc.Error as exc:
            print(f'  FAIL {label}: {exc}')
            return False

    print('\n--- pyodbc ---')
    for db_name in (dbc.get('database', 'WASCO_Calibration'), max_dbc.get('database', 'ExactMAXWasco')):
        pyodbc_test(f'SQL auth -> {db_name}', db_name, use_sql=True)
        pyodbc_test(f'Win auth -> {db_name}', db_name, use_sql=False)

    print('\n--- SQLAlchemy (app path) ---')
    ok_cal = initialize_database(dbc)
    print(f'initialize_database (WASCO): {ok_cal}')
    print(f'is_calibration_database_available: {db_ops.is_calibration_database_available()}')
    print(f'is_shop_order_database_available: {db_ops.is_shop_order_database_available()}')

    if username and password:
        url = URL.create(
            'mssql+pyodbc',
            username=username,
            password=str(password),
            host=server,
            database=dbc.get('database', 'WASCO_Calibration'),
            query={'driver': driver, 'TrustServerCertificate': 'yes'},
        )
        try:
            engine = create_engine(url, connect_args={'timeout': 5})
            with engine.connect() as conn:
                conn.exec_driver_sql('SELECT 1')
            print('SQLAlchemy URL.create: OK')
        except Exception as exc:
            print(f'SQLAlchemy URL.create: FAIL {exc}')

    if not ok_cal:
        print(
            '\n18456 = SQL Server rejected the login (wrong user/password, disabled login, '
            'or no permission on that database). Fix on PASCAL / with your DBA — not an app bug.'
        )
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
