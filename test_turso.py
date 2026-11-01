
import os
from sqlalchemy import create_engine, text

turso_url = os.environ["TURSO_DATABASE_URL"].strip()
turso_token = os.environ["TURSO_AUTH_TOKEN"].strip()

turso_host = (
    turso_url
    .removeprefix("libsql://")
    .removeprefix("https://")
    .rstrip("/")
)

engine = create_engine(
    f"sqlite+libsql://{turso_host}?secure=true",
    connect_args={"auth_token": turso_token},
    pool_pre_ping=True,
)

with engine.connect() as connection:
    print("Connected to Turso successfully")

    for table in ["users", "schools", "products", "product_images", "orders"]:
        count = connection.execute(
            text(f"SELECT COUNT(*) FROM {table}")
        ).scalar()

        print(f"{table}: {count} records")

print("TURSO CONNECTION TEST PASSED")
