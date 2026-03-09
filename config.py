import os
from datetime import timedelta

class Config:

    SECRET_KEY = os.environ.get("SECRET_KEY", "super-secret-key")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:////tmp/inventory.db"
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "jwt-secret-string")

    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=24)

    UPLOAD_FOLDER = "static/uploads"

    MAX_CONTENT_LENGTH = 16 * 1024 * 1024

    TEST_PAYMENT_MODE = False