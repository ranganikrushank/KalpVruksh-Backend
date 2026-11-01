
import os
from datetime import timedelta


class Config:

    # ===============================
    # SECURITY CONFIG
    # ===============================

    SECRET_KEY = os.environ["SECRET_KEY"]

    # ===============================
    # DATABASE CONFIG
    # ===============================

    # Options: postgres or turso
    # Default: postgres (safe for existing production app)
    DATABASE_BACKEND = os.environ.get(
        "DATABASE_BACKEND", "postgres"
    ).strip().lower()

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    if DATABASE_BACKEND == "postgres":

        # DigitalOcean PostgreSQL
        database_url = os.environ.get("DATABASE_URL", "").strip()

        if not database_url:
            raise ValueError(
                "DATABASE_URL is missing. "
                "Add your DigitalOcean PostgreSQL connection URI "
                "to Heroku Config Vars."
            )

        # SQLAlchemy compatibility
        if database_url.startswith("postgres://"):
            database_url = database_url.replace(
                "postgres://", "postgresql://", 1
            )

        SQLALCHEMY_DATABASE_URI = database_url

        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True
        }

    elif DATABASE_BACKEND == "turso":

        # Turso libSQL
        turso_database_url = os.environ.get(
            "TURSO_DATABASE_URL", ""
        ).strip()

        turso_auth_token = os.environ.get(
            "TURSO_AUTH_TOKEN", ""
        ).strip()

        if not turso_database_url:
            raise ValueError(
                "TURSO_DATABASE_URL is missing."
            )

        if not turso_auth_token:
            raise ValueError(
                "TURSO_AUTH_TOKEN is missing."
            )

        if any(
            char.isspace()
            for char in turso_auth_token
        ):
            raise ValueError(
                "TURSO_AUTH_TOKEN contains whitespace."
            )

        turso_host = (
            turso_database_url
            .removeprefix("libsql://")
            .removeprefix("https://")
            .rstrip("/")
        )

        if (
            not turso_host
            or "/" in turso_host
            or "?" in turso_host
            or ":" in turso_host
        ):
            raise ValueError(
                "Invalid TURSO_DATABASE_URL."
            )

        SQLALCHEMY_DATABASE_URI = (
            f"sqlite+libsql://{turso_host}?secure=true"
        )

        SQLALCHEMY_ENGINE_OPTIONS = {
            "connect_args": {
                "auth_token": turso_auth_token
            },
            "pool_pre_ping": True
        }

    else:
        raise ValueError(
            "DATABASE_BACKEND must be 'postgres' or 'turso'."
        )

    # ===============================
    # JWT CONFIG
    # ===============================

    JWT_SECRET_KEY = os.environ.get(
        "JWT_SECRET_KEY", "jwt-secret-string"
    )

    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=24)

    # ===============================
    # FILE UPLOAD CONFIG
    # ===============================

    UPLOAD_FOLDER = "static/uploads"

    MAX_CONTENT_LENGTH = 16 * 1024 * 1024

    # ===============================
    # PAYMENT MODE
    # ===============================

    TEST_PAYMENT_MODE = False
