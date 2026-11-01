
"""One-time, guarded PostgreSQL -> pre-created Turso schema migration.

Requires: PG_DATABASE_URL, TURSO_DATABASE_URL, TURSO_AUTH_TOKEN.
This script does not modify PostgreSQL. Turso target must be empty.
Do not run while writes are happening if you intend to cut over immediately.
"""
import datetime
import decimal
import json
import os
import re
import uuid

import libsql_client
import psycopg2
from psycopg2 import sql

TABLES = [
    'users', 'sellers', 'schools', 'products', 'seller_payments',
    'seller_inventory', 'school_inventory', 'shipments', 'shipment_items',
    'orders', 'order_items', 'staff_orders', 'staff_order_items',
    'product_sizes', 'audit_logs', 'admin_dispatch_instructions',
    'product_images', 'seller_school_products', 'inventory_ledger',
    'school_stock_requests',
]
BATCH_SIZE = 50


def convert(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat(sep=' ') if isinstance(value, datetime.datetime) else value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, memoryview):
        return bytes(value)
    if isinstance(value, (str, int, float, bytes)):
        return value
    raise TypeError(f'Unsupported PostgreSQL value type: {type(value).__name__}')


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def main():
    pg_url = os.environ['PG_DATABASE_URL']
    turso_url = os.environ['TURSO_DATABASE_URL']
    token = os.environ['TURSO_AUTH_TOKEN']
    if turso_url.startswith('libsql://'):
        turso_url = 'https://' + turso_url[len('libsql://'):]
    elif not turso_url.startswith('https://'):
        raise ValueError('TURSO_DATABASE_URL must start with libsql:// or https://')
    if not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::443)?/?', turso_url):
        raise ValueError('Invalid Turso URL')

    with psycopg2.connect(pg_url, sslmode='require') as pg, \
            libsql_client.create_client_sync(turso_url, auth_token=token) as turso:
        pg.set_session(readonly=True, isolation_level='REPEATABLE READ')
        with pg.cursor() as cur:
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            source_tables = {row[0] for row in cur.fetchall()}
            missing = set(TABLES) - source_tables
            if missing:
                raise RuntimeError(f'Missing PostgreSQL tables: {sorted(missing)}')
            unexpected = source_tables - set(TABLES)
            if unexpected:
                raise RuntimeError(f'Extra PostgreSQL tables need review: {sorted(unexpected)}')

            # Discover dependency order so parent records are inserted first.
            cur.execute("""
                SELECT tc.table_name, ccu.table_name AS parent
                FROM information_schema.table_constraints tc
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.constraint_schema = tc.constraint_schema
                WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public'
            """)
            parents = {t: set() for t in TABLES}
            for child, parent in cur.fetchall():
                if child in parents and parent in parents and child != parent:
                    parents[child].add(parent)
            ordered = []
            while parents:
                ready = sorted(t for t, deps in parents.items() if not deps)
                if not ready:
                    raise RuntimeError('Circular foreign keys detected: manual migration required')
                for table in ready:
                    ordered.append(table)
                    parents.pop(table)
                for deps in parents.values():
                    deps.difference_update(ready)

            # Fail before copying if any destination table has rows or schema mismatch.
            source_columns = {}
            source_counts = {}
            for table in ordered:
                cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=%s
                    ORDER BY ordinal_position
                """, (table,))
                columns = [r[0] for r in cur.fetchall()]
                info = turso.execute(f'PRAGMA table_info({quote(table)})')
                target_columns = [r[1] for r in info.rows]
                if not target_columns:
                    raise RuntimeError(f'Missing Turso table: {table}')
                if set(columns) != set(target_columns):
                    raise RuntimeError(
                        f'Schema mismatch in {table}; source-only={sorted(set(columns)-set(target_columns))}, '
                        f'target-only={sorted(set(target_columns)-set(columns))}'
                    )
                target_count = turso.execute(f'SELECT COUNT(*) FROM {quote(table)}').rows[0][0]
                if target_count != 0:
                    raise RuntimeError(f'Turso table {table} is not empty ({target_count} rows); stopping')
                cur.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table)))
                source_counts[table] = cur.fetchone()[0]
                source_columns[table] = columns
            print('Preflight passed: 20 matching, empty Turso tables.')

        for index, table in enumerate(ordered):
            columns = source_columns[table]
            column_sql = ', '.join(quote(c) for c in columns)
            placeholders = ', '.join('?' for _ in columns)
            insert_sql = f'INSERT INTO {quote(table)} ({column_sql}) VALUES ({placeholders})'
            transferred = 0
            with pg.cursor(name=f'migrate_{index}') as stream:
                stream.itersize = BATCH_SIZE
                stream.execute(sql.SQL('SELECT {} FROM {}').format(
                    sql.SQL(', ').join(sql.Identifier(c) for c in columns),
                    sql.Identifier(table)
                ))
                while True:
                    rows = stream.fetchmany(BATCH_SIZE)
                    if not rows:
                        break
                    statements = [libsql_client.Statement(insert_sql, [convert(v) for v in row]) for row in rows]
                    # SDK batch is transactional: all statements succeed or fail together.
                    turso.batch(statements)
                    transferred += len(rows)
                    if transferred % 1000 < BATCH_SIZE:
                        print(f'{table}: {transferred}/{source_counts[table]}')
            if transferred != source_counts[table]:
                raise RuntimeError(f'Count changed for {table}: {transferred} != {source_counts[table]}')
            dest_count = turso.execute(f'SELECT COUNT(*) FROM {quote(table)}').rows[0][0]
            if dest_count != transferred:
                raise RuntimeError(f'Count mismatch for {table}: PostgreSQL={transferred}, Turso={dest_count}')
            print(f'OK {table}: {transferred} records')

        violations = turso.execute('PRAGMA foreign_key_check').rows
        if violations:
            raise RuntimeError(f'Turso foreign key violations: {len(violations)}')
        print('SUCCESS: all 20 table counts match and foreign_key_check passed.')
        print('Do not cut over without verifying totals, indexes, constraints and live-write synchronization.')


if __name__ == '__main__':
    main()
