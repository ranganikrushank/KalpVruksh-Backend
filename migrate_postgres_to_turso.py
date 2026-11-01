
"""One-time PostgreSQL -> Turso migration into an existing EMPTY schema.

Required GitHub Actions secrets:
    PG_DATABASE_URL
    TURSO_DATABASE_URL
    TURSO_AUTH_TOKEN

PostgreSQL is read-only.
Do not rerun after a partial import without cleanup.
Keep Heroku on PostgreSQL until migration is verified.
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
    "users",
    "sellers",
    "schools",
    "products",
    "seller_payments",
    "seller_inventory",
    "school_inventory",
    "shipments",
    "shipment_items",
    "orders",
    "order_items",
    "staff_orders",
    "staff_order_items",
    "product_sizes",
    "audit_logs",
    "admin_dispatch_instructions",
    "product_images",
    "seller_school_products",
    "inventory_ledger",
    "school_stock_requests",
]

BATCH_SIZE = 50


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def convert(value):
    if value is None:
        return None

    if isinstance(value, (dict, list)):
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":")
        )

    if isinstance(value, decimal.Decimal):
        return str(value)

    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ")

    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()

    if isinstance(value, uuid.UUID):
        return str(value)

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, memoryview):
        return bytes(value)

    if isinstance(value, (str, int, float, bytes)):
        return value

    raise TypeError(
        f"Unsupported PostgreSQL value type: {type(value).__name__}"
    )


def read_settings():
    pg_url = os.environ["PG_DATABASE_URL"].strip()
    turso_url = os.environ["TURSO_DATABASE_URL"].strip()
    token = os.environ["TURSO_AUTH_TOKEN"].strip()

    if not token:
        raise ValueError(
            "TURSO_AUTH_TOKEN is empty. Update the GitHub secret."
        )

    if any(ord(char) < 32 or ord(char) == 127 for char in token):
        raise ValueError(
            "TURSO_AUTH_TOKEN contains invalid control characters. "
            "Generate a fresh token and update the GitHub secret."
        )

    if any(char.isspace() for char in token):
        raise ValueError(
            "TURSO_AUTH_TOKEN contains whitespace. "
            "Use a single-line token."
        )

    if not pg_url:
        raise ValueError("PG_DATABASE_URL is empty")

    if turso_url.startswith("libsql://"):
        turso_url = (
            "https://" + turso_url[len("libsql://"):]
        )

    if not re.fullmatch(
        r"https://[A-Za-z0-9.-]+(?::443)?/?",
        turso_url
    ):
        raise ValueError(
            "Invalid TURSO_DATABASE_URL. "
            "Use a valid libsql:// or https:// Turso URL."
        )

    return pg_url, turso_url, token


def dependency_order(cursor):
    cursor.execute("""
        SELECT
            tc.table_name,
            ccu.table_name AS parent
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
            AND ccu.constraint_schema = tc.constraint_schema
        WHERE
            tc.constraint_type = 'FOREIGN KEY'
            AND tc.table_schema = 'public'
    """)

    dependencies = {
        table: set()
        for table in TABLES
    }

    for child, parent in cursor.fetchall():
        if (
            child in dependencies
            and parent in dependencies
            and child != parent
        ):
            dependencies[child].add(parent)

    ordered = []

    while dependencies:
        ready = sorted(
            table
            for table, deps in dependencies.items()
            if not deps
        )

        if not ready:
            raise RuntimeError(
                "Circular foreign keys detected. "
                "Manual migration planning required."
            )

        for table in ready:
            ordered.append(table)
            dependencies.pop(table)

        for deps in dependencies.values():
            deps.difference_update(ready)

    return ordered


def main():
    pg_url, turso_url, token = read_settings()

    print("Starting PostgreSQL to Turso migration...", flush=True)

    with psycopg2.connect(
        pg_url,
        sslmode="require"
    ) as pg, libsql_client.create_client_sync(
        turso_url,
        auth_token=token
    ) as turso:

        pg.set_session(
            readonly=True,
            isolation_level="REPEATABLE READ"
        )

        source_columns = {}
        source_counts = {}

        with pg.cursor() as cur:

            cur.execute("""
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
            """)

            source_tables = {
                row[0]
                for row in cur.fetchall()
            }

            missing = set(TABLES) - source_tables
            extra = source_tables - set(TABLES)

            if missing or extra:
                raise RuntimeError(
                    "PostgreSQL table mismatch: "
                    f"missing={sorted(missing)}, "
                    f"extra={sorted(extra)}"
                )

            ordered = dependency_order(cur)

            for table in ordered:

                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE
                        table_schema = 'public'
                        AND table_name = %s
                    ORDER BY ordinal_position
                """, (table,))

                columns = [
                    row[0]
                    for row in cur.fetchall()
                ]

                result = turso.execute(
                    f"PRAGMA table_info({quote(table)})"
                )

                target_columns = [
                    row[1]
                    for row in result.rows
                ]

                if not target_columns:
                    raise RuntimeError(
                        f"Turso table is missing: {table}"
                    )

                if set(columns) != set(target_columns):
                    raise RuntimeError(
                        f"Column mismatch for {table}: "
                        f"PostgreSQL-only="
                        f"{sorted(set(columns) - set(target_columns))}, "
                        f"Turso-only="
                        f"{sorted(set(target_columns) - set(columns))}"
                    )

                target_count = turso.execute(
                    f"SELECT COUNT(*) FROM {quote(table)}"
                ).rows[0][0]

                if target_count:
                    raise RuntimeError(
                        f"Turso table {table} already contains "
                        f"{target_count} rows. Aborting."
                    )

                cur.execute(
                    sql.SQL(
                        "SELECT COUNT(*) FROM {}"
                    ).format(
                        sql.Identifier(table)
                    )
                )

                source_counts[table] = cur.fetchone()[0]
                source_columns[table] = columns

                print(
                    f"Checked {table}: "
                    f"{source_counts[table]} source rows",
                    flush=True
                )

        print(
            "Preflight passed: all 20 tables exist, "
            "column names match, and Turso tables are empty.",
            flush=True
        )

        for index, table in enumerate(ordered):

            columns = source_columns[table]

            insert_sql = (
                f"INSERT INTO {quote(table)} "
                f"({', '.join(quote(column) for column in columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})"
            )

            copied = 0

            print(
                f"Copying table: {table}",
                flush=True
            )

            with pg.cursor(
                name=f"migrate_{index}"
            ) as stream:

                stream.itersize = BATCH_SIZE

                stream.execute(
                    sql.SQL(
                        "SELECT {} FROM {}"
                    ).format(
                        sql.SQL(", ").join(
                            sql.Identifier(column)
                            for column in columns
                        ),
                        sql.Identifier(table)
                    )
                )

                while True:
                    rows = stream.fetchmany(BATCH_SIZE)

                    if not rows:
                        break

                    statements = [
                        libsql_client.Statement(
                            insert_sql,
                            [
                                convert(value)
                                for value in row
                            ]
                        )
                        for row in rows
                    ]

                    turso.batch(statements)

                    copied += len(rows)

                    if copied % 1000 < BATCH_SIZE:
                        print(
                            f"{table}: copied "
                            f"{copied}/{source_counts[table]}",
                            flush=True
                        )

            target_count = turso.execute(
                f"SELECT COUNT(*) FROM {quote(table)}"
            ).rows[0][0]

            if (
                copied != source_counts[table]
                or target_count != copied
            ):
                raise RuntimeError(
                    f"Row-count mismatch for {table}: "
                    f"source={source_counts[table]}, "
                    f"copied={copied}, "
                    f"destination={target_count}"
                )

            print(
                f"OK {table}: {copied} rows",
                flush=True
            )

        violations = turso.execute(
            "PRAGMA foreign_key_check"
        ).rows

        if violations:
            raise RuntimeError(
                "Foreign-key violations detected in Turso: "
                f"{len(violations)}"
            )

        print(
            "SUCCESS: all 20 table row counts match "
            "and foreign_key_check returned no violations.",
            flush=True
        )

        print(
            "Next: validate data types, indexes, "
            "business totals, and final production "
            "write synchronization.",
            flush=True
        )


if __name__ == "__main__":
    main()
