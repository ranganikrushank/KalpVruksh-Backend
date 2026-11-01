
import os

# Select Turso before importing application configuration.
os.environ["DATABASE_BACKEND"] = "turso"

from flask import Flask
from config import Config
from models import db, User, Product, School, Order, ProductImage

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

with app.app_context():
    print("Testing Flask SQLAlchemy models...")

    for model in [User, Product, School, Order, ProductImage]:
        count = db.session.query(model).count()
        print(f"{model.__tablename__}: {count} records")

    user = db.session.query(User).first()

    if user:
        print("User model loaded successfully")
        print("User role:", user.role.value)

    product = db.session.query(Product).first()

    if product:
        print("Product model loaded successfully")
        print("Product ID:", product.id)

    print("FLASK TURSO MODEL TEST PASSED")
