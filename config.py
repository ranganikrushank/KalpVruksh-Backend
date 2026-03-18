import os
from datetime import timedelta


class Config:

    SECRET_KEY = os.environ.get("SECRET_KEY", "super-secret-key")

    # ===============================
    # DATABASE CONFIG (DigitalOcean PostgreSQL)
    # ===============================
    database_url = os.environ.get("DATABASE_URL")

    # Fix Heroku-style postgres URL
    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    SQLALCHEMY_DATABASE_URI = database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Prevent connection drop issues (important for DO)
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True
    }

    # ===============================
    # JWT CONFIG
    # ===============================
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "jwt-secret-string")
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