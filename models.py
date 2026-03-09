import enum
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

# ================= ENUMS =================

class UserRole(enum.Enum):
    SUPER_ADMIN = "super_admin"
    SELLER = "seller"
    SCHOOL = "school"
    STUDENT = "student"


class ShipmentStatus(enum.Enum):
    ON_THE_WAY = "ON_THE_WAY"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    DELIVERED = "DELIVERED"


class CategoryType(enum.Enum):
    STUDENT = "student"
    STAFF = "staff"


# ================= USER =================

class User(db.Model):

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)

    full_name = db.Column(db.String(120), nullable=False)

    # Students login with phone
    phone_number = db.Column(db.String(20), unique=True, nullable=True)

    # Admin / School / Seller login with username
    username = db.Column(db.String(80), unique=True, nullable=True)

    email = db.Column(db.String(120), nullable=True)

    # 🔐 PASSWORD HASH COLUMN (THIS WAS MISSING)
    password_hash = db.Column(db.String(255), nullable=False)

    role = db.Column(db.Enum(UserRole), nullable=False)

    is_active = db.Column(db.Boolean, default=True)

    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True)
    seller_id = db.Column(db.Integer, db.ForeignKey("sellers.id"), nullable=True)

    expo_push_token = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ================================
    # PASSWORD METHODS
    # ================================

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    # ================================
    # OPTIONAL HELPER (GOOD PRACTICE)
    # ================================

    def to_dict(self):
        return {
            "id": self.id,
            "full_name": self.full_name,
            "phone_number": self.phone_number,
            "username": self.username,
            "role": self.role.value,
            "school_id": self.school_id,
            "seller_id": self.seller_id,
            "created_at": self.created_at.isoformat()
        }


# ================= SELLER =================

class Seller(db.Model):
    __tablename__ = "sellers"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(200), nullable=False)
    company_name = db.Column(db.String(200))
    contact_person = db.Column(db.String(100))
    contact_phone = db.Column(db.String(20))
    contact_email = db.Column(db.String(120))
    address = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    users = db.relationship("User", backref="seller_info", lazy=True)
    inventory = db.relationship("SellerInventory", backref="seller", cascade="all, delete-orphan", lazy=True)
    shipments = db.relationship("Shipment", backref="from_seller", lazy=True)


# ================= SCHOOL =================

class School(db.Model):
    __tablename__ = "schools"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(200), nullable=False)
    address = db.Column(db.Text)
    contact_person = db.Column(db.String(100))
    contact_phone = db.Column(db.String(20))
    contact_email = db.Column(db.String(120))

    commission_percentage = db.Column(db.Float, default=0.0)
    coin_balance = db.Column(db.Float, default=0.0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    users = db.relationship("User", backref="school_info", lazy=True)
    inventory = db.relationship("SchoolInventory", backref="school", cascade="all, delete-orphan", lazy=True)
    shipments = db.relationship("Shipment", backref="to_school", lazy=True)
    staff_orders = db.relationship("StaffOrder", backref="school", lazy=True)
    student_orders = db.relationship("Order", backref="school", lazy=True)


# ================= PRODUCT =================

class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    sku = db.Column(db.String(100), unique=True, nullable=False)
    category = db.Column(db.String(50))
    unit_price = db.Column(db.Float, default=0.0)
    image_url = db.Column(db.String(500))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sizes = db.relationship("ProductSize", backref="product", cascade="all, delete-orphan", lazy=True)
    seller_inventory = db.relationship("SellerInventory", backref="product", lazy=True)
    school_inventory = db.relationship("SchoolInventory", backref="product", lazy=True)
    shipment_items = db.relationship("ShipmentItem", backref="product", lazy=True)
    order_items = db.relationship("OrderItem", backref="product", lazy=True)


# ================= INVENTORY =================

class SellerInventory(db.Model):

    __tablename__ = "seller_inventory"

    id = db.Column(db.Integer, primary_key=True)

    seller_id = db.Column(
        db.Integer,
        db.ForeignKey("sellers.id"),
        nullable=False
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False
    )

    size_id = db.Column(
        db.Integer,
        db.ForeignKey("product_sizes.id"),
        nullable=False
    )

    size = db.relationship("ProductSize")

    total_allocated = db.Column(db.Integer, default=0)
    sent_stock = db.Column(db.Integer, default=0)
    remaining_stock = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("seller_id", "product_id", "size_id"),
    )

class SchoolInventory(db.Model):
    __tablename__ = "school_inventory"

    id = db.Column(db.Integer, primary_key=True)

    # ===============================
    # RELATION KEYS
    # ===============================

    school_id = db.Column(
        db.Integer,
        db.ForeignKey("schools.id"),
        nullable=False
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False
    )

    # 🔥 NEW — SIZE SUPPORT
    size_id = db.Column(
        db.Integer,
        db.ForeignKey("product_sizes.id"),
        nullable=False
    )

    category = db.Column(
        db.Enum(CategoryType),
        nullable=False
    )

    # Relationships
    size = db.relationship("ProductSize")

    # ===============================
    # CURRENT STOCK
    # ===============================

    quantity = db.Column(
        db.Integer,
        default=0,
        nullable=False
    )

    # ===============================
    # TRACKING METRICS
    # ===============================

    total_received = db.Column(
        db.Integer,
        default=0,
        nullable=False
    )

    total_sold = db.Column(
        db.Integer,
        default=0,
        nullable=False
    )

    total_adjusted = db.Column(
        db.Integer,
        default=0,
        nullable=False
    )

    # ===============================
    # REPLENISHMENT CONTROL
    # ===============================

    low_stock_threshold = db.Column(
        db.Integer,
        default=10,
        nullable=False
    )

    last_restocked_at = db.Column(db.DateTime)

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    # 🔥 UNIQUE PER SCHOOL + PRODUCT + SIZE + CATEGORY
    __table_args__ = (
        db.UniqueConstraint(
            "school_id",
            "product_id",
            "size_id",
            "category",
            name="unique_school_product_size_category"
        ),
    )

    # ===================================================
    # SAFE HELPER METHODS
    # ===================================================

    def receive_stock(self, qty: int):
        if qty <= 0:
            raise ValueError("Receive quantity must be positive")

        self.quantity = (self.quantity or 0) + qty
        self.total_received = (self.total_received or 0) + qty
        self.last_restocked_at = datetime.utcnow()

    def sell_stock(self, qty: int):
        if qty <= 0:
            raise ValueError("Sell quantity must be positive")

        if qty > (self.quantity or 0):
            raise ValueError("Insufficient stock")

        self.quantity = (self.quantity or 0) - qty
        self.total_sold = (self.total_sold or 0) + qty

    def adjust_stock(self, qty: int):
        if qty == 0:
            return

        new_quantity = self.quantity + qty

        if new_quantity < 0:
            raise ValueError("Adjustment would result in negative stock")

        self.quantity = new_quantity
        self.total_adjusted += qty

    def is_low_stock(self) -> bool:
        return self.quantity <= self.low_stock_threshold

# ================= SHIPMENTS =================

class Shipment(db.Model):
    __tablename__ = "shipments"

    id = db.Column(db.Integer, primary_key=True)

    from_seller_id = db.Column(db.Integer, db.ForeignKey("sellers.id"), nullable=False)
    to_school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=False)

    status = db.Column(db.Enum(ShipmentStatus), default=ShipmentStatus.ON_THE_WAY)

    tracking_number = db.Column(db.String(100))
    notes = db.Column(db.Text)

    estimated_delivery = db.Column(db.DateTime)
    actual_delivery = db.Column(db.DateTime)
    received_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship("ShipmentItem", backref="shipment", lazy=True)


class ShipmentItem(db.Model):
    __tablename__ = "shipment_items"

    id = db.Column(db.Integer, primary_key=True)

    shipment_id = db.Column(db.Integer, db.ForeignKey("shipments.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    
    size_id = db.Column(
        db.Integer,
        db.ForeignKey("product_sizes.id"),
        nullable=True
    )

    size = db.relationship("ProductSize")

    category = db.Column(db.Enum(CategoryType), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, default=0.0)


# ================= ORDERS =================

class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)

    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    # ⭐ ADD THIS
    student = db.relationship("User", foreign_keys=[student_id])

    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=False)

    total_amount = db.Column(db.Float, default=0.0)

    status = db.Column(db.String(50), default="pending")

    payment_status = db.Column(db.String(50), default="PENDING")

    payment_mode = db.Column(db.String(20), nullable=False, default="ONLINE")

    payment_id = db.Column(db.String(255))
    confirmation_token = db.Column(db.String(20), unique=True)
    pickup_code = db.Column(db.String(6))
    pickup_confirmed_at = db.Column(db.DateTime)

    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)

    items = db.relationship("OrderItem", backref="order", lazy=True)


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)

    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)

    size_id = db.Column(
        db.Integer,
        db.ForeignKey("product_sizes.id"),
        nullable=False
    )

    size = db.relationship("ProductSize")

    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, default=0.0)
    total_price = db.Column(db.Float, default=0.0)


# ================= STAFF ORDERS =================

class StaffOrder(db.Model):
    __tablename__ = "staff_orders"

    id = db.Column(db.Integer, primary_key=True)

    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=False)

    total_amount = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default="pending")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)

    items = db.relationship("StaffOrderItem", backref="staff_order", lazy=True)


class StaffOrderItem(db.Model):
    __tablename__ = "staff_order_items"

    id = db.Column(db.Integer, primary_key=True)

    staff_order_id = db.Column(
        db.Integer,
        db.ForeignKey("staff_orders.id"),
        nullable=False
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False
    )
    
    size_id = db.Column(
        db.Integer,
        db.ForeignKey("product_sizes.id"),
        nullable=False
    )

    size = db.relationship("ProductSize") 

    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    total_price = db.Column(db.Float, nullable=False)

    # ✅ ADD THIS RELATIONSHIP
    product = db.relationship("Product", lazy="joined")


# ================= PRODUCT SIZE =================

class ProductSize(db.Model):
    __tablename__ = "product_sizes"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)

    size = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ================= AUDIT LOG =================

class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(100), nullable=False)

    table_name = db.Column(db.String(50))
    record_id = db.Column(db.Integer)

    old_values = db.Column(db.JSON)
    new_values = db.Column(db.JSON)

    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# ================= DISPATCH =================

class AdminDispatchInstruction(db.Model):
    __tablename__ = "admin_dispatch_instructions"

    id = db.Column(db.Integer, primary_key=True)

    seller_id = db.Column(db.Integer, db.ForeignKey("sellers.id"), nullable=False)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)

    seller = db.relationship("Seller", backref="dispatch_instructions")
    school = db.relationship("School", backref="dispatch_instructions")
    product = db.relationship("Product", backref="dispatch_instructions")

    category = db.Column(db.String(10), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    size_id = db.Column(db.Integer, db.ForeignKey("product_sizes.id"), nullable=True)
    size = db.relationship("ProductSize")

    status = db.Column(db.String(20), default="PENDING")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    fulfilled_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    
class ProductImage(db.Model):
    __tablename__ = "product_images"

    id = db.Column(db.Integer, primary_key=True)

    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True)

    image_url = db.Column(db.String(255), nullable=False)
    display_order = db.Column(db.Integer, default=1)

    product = db.relationship("Product", backref="images")
    school = db.relationship("School")
    
class SellerSchoolProduct(db.Model):
    __tablename__ = "seller_school_products"

    id = db.Column(db.Integer, primary_key=True)

    seller_id = db.Column(
        db.Integer,
        db.ForeignKey("sellers.id"),
        nullable=False
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False
    )

    school_id = db.Column(
        db.Integer,
        db.ForeignKey("schools.id"),
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    # Relationships
    seller = db.relationship("Seller", backref="seller_school_products")
    product = db.relationship("Product", backref="seller_school_products")
    school = db.relationship("School", backref="seller_school_products")

    # Prevent duplicates
    __table_args__ = (
        db.UniqueConstraint(
            "seller_id",
            "product_id",
            "school_id",
            name="unique_seller_product_school"
        ),
    )
    
# ================= INVENTORY LEDGER =================
from datetime import datetime, timezone

class InventoryLedger(db.Model):

    __tablename__ = "inventory_ledger"

    id = db.Column(db.Integer, primary_key=True)

    product_id = db.Column(db.Integer, db.ForeignKey("products.id"))

    size_id = db.Column(
        db.Integer,
        db.ForeignKey("product_sizes.id"),
        nullable=True
    )

    seller_id = db.Column(db.Integer, db.ForeignKey("sellers.id"), nullable=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True)

    action = db.Column(db.String(100))

    quantity = db.Column(db.Integer)
    balance_after = db.Column(db.Integer)

    reference_type = db.Column(db.String(50))
    reference_id = db.Column(db.Integer)

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc)
    )

    size = db.relationship("ProductSize")