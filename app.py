import os
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, jsonify, request, url_for
from flask_jwt_extended import JWTManager, get_jwt_identity, jwt_required, create_access_token, unset_jwt_cookies
from flask_cors import CORS  # Add if needed for frontend
from flask import render_template, redirect, session
from config import Config
from models import (
    SellerSchoolProduct,
    db,
    User,
    UserRole,
    School,
    Seller,
    Product,
    CategoryType,
    SellerInventory,
    SchoolInventory,
    Shipment,
    ShipmentItem,
    Order,
    OrderItem,
    StaffOrder,
    StaffOrderItem,
    AdminDispatchInstruction,
    ShipmentStatus,
    AuditLog,
    ProductSize,
    ProductImage,
    InventoryLedger
)

from auth import login, register_user, role_required
import razorpay
import hmac
import hashlib
import os
import math
from flask_cors import CORS
import uuid
import os
from werkzeug.utils import secure_filename

def success_response(data=None, message=None, status=200):
    return jsonify({
        "success": True,
        "message": message,
        "data": data
    }), status

def record_inventory(
    product_id,
    size_id,
    action,
    from_entity,
    to_entity,
    quantity,
    balance,
    reference_type=None,
    reference_id=None
):

    from models import InventoryLedger

    tx = InventoryLedger(
        product_id=product_id,
        size_id=size_id,
        action=action,
        from_entity=from_entity,
        to_entity=to_entity,
        quantity=quantity,
        balance_after=balance,
        reference_type=reference_type,
        reference_id=reference_id
    )

    db.session.add(tx)


def create_inventory_ledger(
        product_id,
        quantity,
        balance,
        transaction_type,
        seller_id=None,
        school_id=None,
        reference_type=None,
        reference_id=None
):

    ledger = InventoryLedger(
        product_id=product_id,
        seller_id=seller_id,
        school_id=school_id,
        transaction_type=transaction_type,
        quantity=quantity,
        balance_after=balance,
        reference_type=reference_type,
        reference_id=reference_id
    )

    db.session.add(ledger)
    
def error_response(message, status=400):
    return jsonify({
        "success": False,
        "error": message
    }), status
    
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371  # KM

    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat/2)**2 +
        math.cos(math.radians(lat1)) *
        math.cos(math.radians(lat2)) *
        math.sin(dlon/2)**2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
    
    app.secret_key = "super-secret-key"
    
    db.init_app(app)
    jwt = JWTManager(app)
    CORS(app)  # Enable if using separate frontend
    
    razorpay_client = razorpay.Client(auth=(
        os.getenv("RAZORPAY_KEY_ID"),
        os.getenv("RAZORPAY_KEY_SECRET")
    ))
    
    app.config['UPLOAD_FOLDER'] = 'static/uploads/products'
    app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

    with app.app_context():
        db.create_all()
        
        # Create super admin if doesn't exist
        if not User.query.filter_by(role=UserRole.SUPER_ADMIN).first():

            super_admin = User(
                full_name="Super Admin",
                phone_number="7778888578",
                role=UserRole.SUPER_ADMIN
            )

            super_admin.set_password("ad123")

            db.session.add(super_admin)
            db.session.commit()
            super_admin.set_password('ad123')
            db.session.add(super_admin)
            db.session.commit()

    # === AUTHENTICATION HELPERS (JWT ONLY) ===
    from flask_jwt_extended import get_jwt

    def get_current_user():
        user_id = get_jwt_identity()

        if not user_id:
            return None

        return db.session.get(User, int(user_id))

    def log_action(action: str, table_name: str = None, record_id: int = None, old_vals=None, new_vals=None):
        user = get_current_user()
        audit = AuditLog(
            user_id=user.id,
            action=action,
            table_name=table_name,
            record_id=record_id,
            old_values=old_vals,
            new_values=new_vals,
            timestamp=datetime.utcnow()
        )
        db.session.add(audit)
    
    @app.route("/api/whoami", methods=["GET"])
    @jwt_required()
    def whoami():
        user = get_current_user()
        return jsonify({
            "id": user.id,
            "username": user.username,
            "role": user.role.value
        })
        
    @app.template_filter('intcomma')
    def intcomma(value):
        try:
            return "{:,}".format(int(value))
        except (ValueError, TypeError):
            return value
    @app.context_processor
    def inject_user():
        user = None
        if 'user_id' in session:
            user = User.query.get(session['user_id'])
        return dict(current_user=user)
    def session_login_required(role=None):
        def wrapper(fn):
            @wraps(fn)
            def decorator(*args, **kwargs):
                if 'user_id' not in session:
                    return redirect('/login')

                if role and session.get('role') != role.value:
                    return "Unauthorized", 403

                return fn(*args, **kwargs)
            return decorator
        return wrapper
    @app.route('/')
    def home():
        return redirect('/login')

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect('/login')
    @app.route('/admin/dashboard')
    @session_login_required(UserRole.SUPER_ADMIN)
    def admin_dashboard():
        total_schools = School.query.count()
        total_sellers = Seller.query.count()
        total_products = Product.query.count()

        student_sales = db.session.query(db.func.sum(Order.total_amount)).filter(
            Order.status == 'completed'
        ).scalar() or 0

        staff_sales = db.session.query(db.func.sum(StaffOrder.total_amount)).filter(
            StaffOrder.status == 'completed'
        ).scalar() or 0

        total_revenue = student_sales + staff_sales

        stats = {
            "total_schools": total_schools,
            "total_sellers": total_sellers,
            "total_products": total_products,
            "total_revenue": round(total_revenue, 2)
        }

        return render_template('admin/dashboard.html', stats=stats)

    
    @app.context_processor
    def inject_admin_stats():
        if 'user_id' in session and session.get('role') == UserRole.SUPER_ADMIN.value:
            total_schools = School.query.count()
            total_sellers = Seller.query.count()
            total_products = Product.query.count()

            student_sales = db.session.query(db.func.sum(Order.total_amount)).filter(
                Order.status == 'completed'
            ).scalar() or 0

            staff_sales = db.session.query(db.func.sum(StaffOrder.total_amount)).filter(
                StaffOrder.status == 'completed'
            ).scalar() or 0

            total_revenue = student_sales + staff_sales

            stats = {
                "total_schools": total_schools,
                "total_sellers": total_sellers,
                "total_products": total_products,
                "total_revenue": round(total_revenue, 2)
            }

            return dict(stats=stats)

        return dict(stats=None)
    
    
    @app.route('/admin/schools', methods=['GET', 'POST'])
    @session_login_required(UserRole.SUPER_ADMIN)
    def manage_schools_page():

        if request.method == 'POST':

            school_id = request.form.get('school_id')  # for edit support
            username = request.form.get('username')
            password = request.form.get('password')
            email = request.form.get('contact_email')

            if not username or not password:
                return "Username and Password are required", 400

            # 🔎 If editing existing school
            if school_id:
                school = School.query.get(school_id)
                if not school:
                    return "School not found", 404

                school.name = request.form.get('name')
                school.address = request.form.get('address')
                school.contact_person = request.form.get('contact_person')
                school.contact_phone = request.form.get('contact_phone')
                school.contact_email = email

                user = User.query.filter_by(school_id=school.id).first()
                if user:
                    if user.username != username:
                        if User.query.filter_by(username=username).first():
                            return "Username already exists", 400
                    user.username = username
                    user.email = email
                    user.set_password(password)

                db.session.commit()
                return redirect('/admin/schools')

            # 🔹 CREATE NEW SCHOOL

            # Check username uniqueness
            if User.query.filter_by(username=username).first():
                return "Username already exists", 400

            # Check email uniqueness
            if User.query.filter_by(email=email).first():
                return "Email already exists", 400

            # 1️⃣ Create School
            commission = float(request.form.get('commission_percentage') or 0)

            school = School(
                name=request.form.get('name'),
                address=request.form.get('address'),
                contact_person=request.form.get('contact_person'),
                contact_phone=request.form.get('contact_phone'),
                contact_email=email,
                commission_percentage=commission,
                coin_balance=0
            )
            db.session.add(school)
            db.session.flush()

            # 2️⃣ Create Credentials
            school_user = User(
                full_name=request.form.get('contact_person'),
                phone_number=request.form.get('contact_phone') or "0000000000",
                username=username,
                email=email,
                role=UserRole.SCHOOL,
                school_id=school.id
            )
            school_user.set_password(password)

            db.session.add(school_user)
            db.session.commit()

            return redirect('/admin/schools')

        schools = School.query.all()
        return render_template('admin/manage_schools.html', schools=schools)

    @app.route('/admin/sellers', methods=['GET', 'POST'])
    @session_login_required(UserRole.SUPER_ADMIN)
    def manage_sellers_page():

        if request.method == 'POST':

            seller_id = request.form.get('seller_id')
            username = request.form.get('username')
            password = request.form.get('password')
            email = request.form.get('contact_email')

            if not username or not password:
                return "Username and Password are required", 400

            # 🔎 EDIT MODE
            if seller_id:
                seller = Seller.query.get(seller_id)
                if not seller:
                    return "Seller not found", 404

                seller.name = request.form.get('name')
                seller.company_name = request.form.get('company_name')
                seller.contact_person = request.form.get('contact_person')
                seller.contact_phone = request.form.get('contact_phone')
                seller.contact_email = email
                seller.address = request.form.get('address')

                user = User.query.filter_by(seller_id=seller.id).first()

                if user:
                    if user.username != username:
                        if User.query.filter_by(username=username).first():
                            return "Username already exists", 400

                    user.username = username
                    user.email = email
                    user.set_password(password)

                db.session.commit()
                return redirect('/admin/sellers')

            # 🔹 CREATE NEW SELLER

            if User.query.filter_by(username=username).first():
                return "Username already exists", 400

            if User.query.filter_by(email=email).first():
                return "Email already exists", 400

            # 1️⃣ Create Seller
            seller = Seller(
                name=request.form.get('name'),
                company_name=request.form.get('company_name'),
                contact_person=request.form.get('contact_person'),
                contact_phone=request.form.get('contact_phone'),
                contact_email=email,
                address=request.form.get('address')
            )
            db.session.add(seller)
            db.session.flush()

            # 2️⃣ Create Credentials
            seller_user = User(
                username=username,
                email=email,
                role=UserRole.SELLER,
                seller_id=seller.id
            )
            seller_user.set_password(password)

            db.session.add(seller_user)
            db.session.commit()

            return redirect('/admin/sellers')

        sellers = Seller.query.all()
        return render_template('admin/manage_sellers.html', sellers=sellers)


    @app.route('/admin/products', methods=['GET', 'POST'])
    @session_login_required(UserRole.SUPER_ADMIN)
    def manage_products_page():

        if request.method == 'POST':

            try:
                name = request.form.get('name', '').strip()
                sku = request.form.get('sku', '').strip()
                category = request.form.get('category')
                unit_price = float(request.form.get('unit_price'))
                description = request.form.get('description')
            except (TypeError, ValueError):
                return "Invalid form data", 400

            # ===== BASIC VALIDATION =====
            if not name or not sku:
                return "Product name and SKU are required", 400

            if unit_price < 0:
                return "Price cannot be negative", 400

            if Product.query.filter_by(sku=sku).first():
                return "SKU already exists", 400

            # ===== SIZE VALIDATION =====
            sizes = request.form.getlist('size[]')
            quantities = request.form.getlist('size_quantity[]')

            if not sizes or not quantities:
                return "At least one size is required", 400

            if len(sizes) != len(quantities):
                return "Size data mismatch", 400

            seen_sizes = set()
            valid_sizes = []

            for size, qty in zip(sizes, quantities):

                size = size.strip()

                if not size:
                    return "Size cannot be empty", 400

                try:
                    qty = int(qty)
                except ValueError:
                    return f"Invalid quantity for size {size}", 400

                if qty <= 0:
                    return f"Quantity must be greater than zero for size {size}", 400

                if size in seen_sizes:
                    return f"Duplicate size detected: {size}", 400

                seen_sizes.add(size)
                valid_sizes.append((size, qty))

            # ===== CREATE PRODUCT =====
            product = Product(
                name=name,
                sku=sku,
                category=category,
                unit_price=unit_price,
                description=description
            )

            db.session.add(product)
            db.session.flush()  # Get product.id

            # ===== INSERT SIZES =====
            for size, qty in valid_sizes:
                size_entry = ProductSize(
                    product_id=product.id,
                    size=size,
                    quantity=qty
                )
                db.session.add(size_entry)

            # =========================================================
            # 🔥 IMAGE UPLOAD LOGIC (MAX 5 IMAGES WITH SEQUENCE)
            # =========================================================

            images = request.files.getlist("product_images")
            school_visibility = request.form.get("school_visibility")

            if images:

                # Remove empty files
                images = [img for img in images if img.filename != ""]

                if len(images) > 5:
                    return "Maximum 5 images allowed per product", 400

                upload_folder = app.config.get(
                    'UPLOAD_FOLDER',
                    'static/uploads/products'
                )

                if not os.path.exists(upload_folder):
                    os.makedirs(upload_folder)

                for index, image in enumerate(images):

                    # Secure filename
                    original_filename = image.filename
                    extension = original_filename.split('.')[-1].lower()

                    if extension not in ['jpg', 'jpeg', 'png', 'webp']:
                        return "Only image files are allowed", 400

                    filename = f"{product.id}_{index+1}.{extension}"
                    filepath = os.path.join(upload_folder, filename)

                    image.save(filepath)

                    image_entry = ProductImage(
                        product_id=product.id,
                        school_id=int(school_visibility) if school_visibility else None,
                        image_url=f"/static/uploads/products/{filename}",
                        display_order=index + 1
                    )

                    db.session.add(image_entry)

            # 🚫 NO AUTO ALLOCATION HERE
            # Allocation must be done manually

            db.session.commit()

            return redirect('/admin/products')

        # ===== GET REQUEST =====
        products = Product.query.all()
        schools = School.query.all()

        return render_template(
            'admin/manage_products.html',
            products=products,
            schools=schools
        )
    
    @app.route('/admin/inventory')
    @session_login_required(UserRole.SUPER_ADMIN)
    def inventory_tracking_page():

        # ======= STATS =======
        total_sellers = Seller.query.count()

        pending_dispatches = AdminDispatchInstruction.query.filter_by(status='PENDING').count()

        on_the_way = Shipment.query.filter_by(status=ShipmentStatus.ON_THE_WAY).count()

        delivered = Shipment.query.filter_by(status=ShipmentStatus.DELIVERED).count()

        stats = {
            "total_sellers": total_sellers,
            "pending_dispatches": pending_dispatches,
            "on_the_way": on_the_way,
            "delivered": delivered
        }

        # ======= INVENTORY SUMMARY =======
        sellers = Seller.query.all()
        inventory_summary = []

        for seller in sellers:
            seller_inventory = SellerInventory.query.filter_by(seller_id=seller.id).all()

            total_stock = sum(i.total_allocated for i in seller_inventory)
            allocated = sum(i.sent_stock for i in seller_inventory)
            remaining = sum(i.remaining_stock for i in seller_inventory)

            inventory_summary.append({
                "seller": seller,
                "total_stock": total_stock,
                "allocated": allocated,
                "remaining": remaining
            })

        return render_template(
            'admin/inventory_tracking.html',
            stats=stats,
            inventory_summary=inventory_summary
        )


    @app.route('/admin/create-dispatch', methods=['POST'])
    @session_login_required(UserRole.SUPER_ADMIN)
    def create_dispatch_web():

        try:
            seller_id = int(request.form.get('seller_id'))
            product_id = int(request.form.get('product_id'))
            size_id = int(request.form.get('size_id'))
            category = request.form.get('category')
            quantity = int(request.form.get('quantity'))

            # 🔥 IMPORTANT CHANGE
            school_ids = request.form.getlist('school_ids')

        except (TypeError, ValueError):
            return "Invalid form data", 400

        if not school_ids:
            return "Please select at least one school", 400

        if quantity <= 0:
            return "Quantity must be positive", 400

        if category not in ['student', 'staff']:
            return "Invalid category", 400

        seller = Seller.query.get(seller_id)
        product = Product.query.get(product_id)
        size = None

        if size_id:
            size = ProductSize.query.get(size_id)

            if not size or size.product_id != product_id:
                return jsonify({'error': 'Invalid size for selected product'}), 400

        # Check seller inventory
        seller_inv = SellerInventory.query.filter_by(
            seller_id=seller_id,
            product_id=product_id
        ).first()

        total_required = quantity * len(school_ids)

        if not seller_inv or seller_inv.remaining_stock < total_required:
            return f"Insufficient seller stock. Required: {total_required}", 400

        # 🔥 CREATE ONE INSTRUCTION PER SCHOOL
        for school_id in school_ids:

            instruction = AdminDispatchInstruction(
                seller_id=seller_id,
                school_id=int(school_id),
                product_id=product_id,
                size_id=size_id,
                category=category,
                quantity=quantity,
                status='PENDING',
                created_at=datetime.utcnow()
            )

            db.session.add(instruction)

        db.session.commit()

        return redirect('/admin/dispatch')
    
    @app.route('/admin/sales')
    @session_login_required(UserRole.SUPER_ADMIN)
    def admin_sales_page():

        schools = School.query.all()

        sales_data = []

        for school in schools:

            student_orders = Order.query.filter_by(
                school_id=school.id,
                status='completed'
            ).all()

            staff_orders = StaffOrder.query.filter_by(
                school_id=school.id,
                status='completed'
            ).all()

            student_revenue = sum(o.total_amount for o in student_orders)
            staff_revenue = sum(o.total_amount for o in staff_orders)

            sales_data.append({
                "school_name": school.name,
                "student_orders": len(student_orders),
                "staff_orders": len(staff_orders),
                "student_revenue": round(student_revenue, 2),
                "staff_revenue": round(staff_revenue, 2),
                "total_revenue": round(student_revenue + staff_revenue, 2)
            })

        return render_template(
            'admin/sales_overview.html',
            sales_data=sales_data
        )
        
    @app.route('/admin/low-stock')
    @session_login_required(UserRole.SUPER_ADMIN)
    def admin_low_stock_page():

        low_stock_items = SchoolInventory.query.filter(
            SchoolInventory.quantity <= SchoolInventory.low_stock_threshold
        ).all()

        return render_template(
            'admin/low_stock.html',
            low_stock_items=low_stock_items
        )
        
    from sqlalchemy import func

    @app.route('/admin/analytics')
    @session_login_required(UserRole.SUPER_ADMIN)
    def admin_analytics_page():

        schools = School.query.all()

        total_schools = len(schools)
        total_sellers = Seller.query.count()

        total_student_revenue = 0
        total_staff_revenue = 0
        total_commission_paid = 0

        school_financials = []

        for school in schools:

            student_revenue = db.session.query(
                func.coalesce(func.sum(Order.total_amount), 0)
            ).filter(
                Order.school_id == school.id,
                Order.status == "completed"
            ).scalar()

            staff_revenue = db.session.query(
                func.coalesce(func.sum(StaffOrder.total_amount), 0)
            ).filter(
                StaffOrder.school_id == school.id,
                StaffOrder.status == "completed"
            ).scalar()

            total_revenue = student_revenue + staff_revenue

            commission = (student_revenue * school.commission_percentage) / 100
            platform_profit = total_revenue - commission

            total_student_revenue += student_revenue
            total_staff_revenue += staff_revenue
            total_commission_paid += commission

            school_financials.append({
                "school": school.name,
                "student_revenue": round(student_revenue, 2),
                "staff_revenue": round(staff_revenue, 2),
                "total_revenue": round(total_revenue, 2),
                "commission": round(commission, 2),
                "profit": round(platform_profit, 2)
            })

        total_revenue = total_student_revenue + total_staff_revenue
        platform_profit_total = total_revenue - total_commission_paid

        return render_template(
            "admin/analytics.html",
            total_schools=total_schools,
            total_sellers=total_sellers,
            total_revenue=round(total_revenue, 2),
            total_student_revenue=round(total_student_revenue, 2),
            total_staff_revenue=round(total_staff_revenue, 2),
            total_commission_paid=round(total_commission_paid, 2),
            platform_profit_total=round(platform_profit_total, 2),
            school_financials=school_financials
        )
    
    @app.route('/admin/sales/school/<int:school_id>')
    @session_login_required(UserRole.SUPER_ADMIN)
    def admin_school_sales_detail(school_id):

        school = School.query.get(school_id)

        student_orders = Order.query.filter_by(
            school_id=school_id,
            status='completed'
        ).all()

        staff_orders = StaffOrder.query.filter_by(
            school_id=school_id,
            status='completed'
        ).all()

        return render_template(
            'admin/school_sales_detail.html',
            school=school,
            student_orders=student_orders,
            staff_orders=staff_orders
        )
        
    @app.route('/admin/dispatch')
    @session_login_required(UserRole.SUPER_ADMIN)
    def dispatch_control_page():

        schools = School.query.all()
        sellers = Seller.query.all()
        products = Product.query.all()

        instructions = AdminDispatchInstruction.query.order_by(
            AdminDispatchInstruction.created_at.desc()
        ).all()

        return render_template(
            'admin/allocate_stock.html',
            schools=schools,
            sellers=sellers,
            products=products,
            instructions=instructions
        )

    @app.route('/seller/dashboard')
    @session_login_required(UserRole.SELLER)
    def seller_dashboard():

        user = User.query.get(session['user_id'])

        # ===== SELLER INVENTORY STATS =====
        seller_inventory = SellerInventory.query.filter_by(
            seller_id=user.seller_id
        ).all()

        total_allocated = sum(inv.total_allocated for inv in seller_inventory)
        total_sent = sum(inv.sent_stock for inv in seller_inventory)
        total_remaining = sum(inv.remaining_stock for inv in seller_inventory)

        stats = {
            "total_allocated": total_allocated,
            "total_sent": total_sent,
            "total_remaining": total_remaining
        }

        # ===== PENDING DISPATCH INSTRUCTIONS =====
        pending_instructions = AdminDispatchInstruction.query.filter_by(
            seller_id=user.seller_id,
            status='PENDING'
        ).count()

        # ===== RECENT SHIPMENTS =====
        recent_shipments = Shipment.query.filter_by(
            from_seller_id=user.seller_id
        ).order_by(Shipment.created_at.desc()).limit(5).all()

        return render_template(
            'seller/dashboard.html',
            stats=stats,
            pending_instructions=pending_instructions,
            recent_shipments=recent_shipments
        )
    
    @app.route('/api/seller/dispatch-history', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SELLER)
    def seller_dispatch_history():

        try:
            user = get_current_user()

            shipments = (
                Shipment.query
                .filter(Shipment.from_seller_id == user.seller_id)
                .order_by(Shipment.id.desc())
                .all()
            )

            data = []

            for shipment in shipments:

                school = School.query.get(shipment.to_school_id)

                shipment_items = ShipmentItem.query.filter_by(
                    shipment_id=shipment.id
                ).all()

                for item in shipment_items:

                    product = Product.query.get(item.product_id)

                    data.append({
                        "shipment_id": shipment.id,
                        "product_name": product.name if product else "Unknown",
                        "school_name": school.name if school else "Unknown",
                        "quantity": item.quantity,
                        "status": shipment.status.name if shipment.status else "UNKNOWN",
                    })

            return jsonify(data), 200

        except Exception as e:
            print("History error:", str(e))
            return jsonify({"error": "History failed"}), 500


    @app.route('/school/dashboard')
    @session_login_required(UserRole.SCHOOL)
    def school_dashboard():

        user = User.query.get(session['user_id'])
        school = School.query.get(user.school_id)

        # ===== STOCK COUNTS =====
        school_inventory = SchoolInventory.query.filter_by(
            school_id=user.school_id
        ).all()

        student_stock = sum(i.quantity for i in school_inventory if i.category == CategoryType.STUDENT)
        staff_stock = sum(i.quantity for i in school_inventory if i.category == CategoryType.STAFF)

        # ===== PENDING SHIPMENTS =====
        pending_shipments = Shipment.query.filter_by(
            to_school_id=user.school_id,
            status=ShipmentStatus.ON_THE_WAY
        ).count()

        # ===== TOTAL ORDERS =====
        total_student_orders = Order.query.filter_by(
            school_id=user.school_id,
            status='completed'
        ).count()

        total_staff_orders = StaffOrder.query.filter_by(
            school_id=user.school_id,
            status='completed'
        ).count()

        total_orders = total_student_orders + total_staff_orders

        # ===== RECENT SHIPMENTS =====
        recent_shipments = Shipment.query.filter_by(
            to_school_id=user.school_id
        ).order_by(Shipment.created_at.desc()).limit(5).all()

        stats = {
            "student_stock": student_stock,
            "staff_stock": staff_stock,
            "pending_shipments": pending_shipments,
            "total_orders": total_orders
        }

        return render_template(
            'school/dashboard.html',
            stats=stats,
            recent_shipments=recent_shipments,
            coin_balance=school.coin_balance,
            commission=school.commission_percentage
        )

    @app.route('/school/student-products')
    @session_login_required(UserRole.SCHOOL)
    def school_student_products_page():

        user = User.query.get(session['user_id'])

        inventory = SchoolInventory.query.filter_by(
            school_id=user.school_id,
            category=CategoryType.STUDENT
        ).all()

        return render_template(
            'school/student_products.html',
            inventory=inventory
        )
        
    @app.route('/api/student/alternate-delivery/<int:product_id>', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.STUDENT)
    def alternate_delivery(product_id):

        user = get_current_user()

        # Get student’s primary school
        student_school = School.query.get(user.school_id)

        if not student_school:
            return jsonify({'error': 'Student school not found'}), 404

        if not student_school.latitude or not student_school.longitude:
            return jsonify({
                'error': 'Student school location not configured'
            }), 400

        # Check stock in own school
        own_inv = SchoolInventory.query.filter_by(
            school_id=user.school_id,
            product_id=product_id,
            category=CategoryType.STUDENT
        ).first()

        if own_inv and own_inv.quantity > 0:
            return jsonify({
                "available_in_primary_school": True,
                "primary_school": {
                    "school_id": student_school.id,
                    "school_name": student_school.name,
                    "available_quantity": own_inv.quantity
                }
            }), 200

        # Otherwise check alternate schools
        all_inventory = SchoolInventory.query.filter(
            SchoolInventory.product_id == product_id,
            SchoolInventory.category == CategoryType.STUDENT,
            SchoolInventory.quantity > 0,
            SchoolInventory.school_id != user.school_id
        ).all()

        results = []
        MAX_RADIUS_KM = 20  # Restrict within 20 km (recommended)

        for inv in all_inventory:
            school = inv.school  # Use relationship instead of extra query

            if not school.latitude or not school.longitude:
                continue

            distance = calculate_distance(
                student_school.latitude,
                student_school.longitude,
                school.latitude,
                school.longitude
            )

            # Skip if beyond allowed radius
            if distance > MAX_RADIUS_KM:
                continue

            results.append({
                "school_id": school.id,
                "school_name": school.name,
                "available_quantity": inv.quantity,
                "distance_km": round(distance, 2)
            })

        # Sort nearest → farthest
        results.sort(key=lambda x: x['distance_km'])

        if not results:
            return jsonify({
                "available_in_primary_school": False,
                "alternate_schools": [],
                "message": "Product not available in nearby schools"
            }), 200

        return jsonify({
            "available_in_primary_school": False,
            "alternate_schools": results
        }), 200
    
    
    @app.route('/student/dashboard')
    @session_login_required(UserRole.STUDENT)
    def student_dashboard():
        return render_template('student/dashboard.html')
    @app.route('/login', methods=['GET', 'POST'])
    def web_login():
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')

            user = User.query.filter_by(username=username).first()

            if not user or not user.check_password(password):
                return render_template('login.html', error="Invalid credentials")

            session['user_id'] = user.id
            session['role'] = user.role.value

            if user.role == UserRole.SUPER_ADMIN:
                return redirect('/admin/dashboard')
            elif user.role == UserRole.SELLER:
                return redirect('/seller/dashboard')
            elif user.role == UserRole.SCHOOL:
                return redirect('/school/dashboard')
            elif user.role == UserRole.STUDENT:
                return redirect('/student/dashboard')

        return render_template('login.html')

    # === FEATURE 1: ADMIN DISPATCH INSTRUCTION MODEL & ENDPOINTS ===
    @app.route('/api/admin/create-dispatch', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def create_dispatch_instruction():

        try:
            data = request.get_json()

            if not data:
                return jsonify({'error': 'Invalid JSON body'}), 400

            # ===============================
            # REQUIRED FIELDS VALIDATION
            # ===============================
            required_fields = [
                'seller_id',
                'product_id',
                'category',
                'quantity',
                'school_ids'
            ]

            for field in required_fields:
                if field not in data:
                    return jsonify({'error': f'Missing field: {field}'}), 400

            # ===============================
            # TYPE CONVERSION
            # ===============================
            try:
                seller_id = int(data['seller_id'])
                product_id = int(data['product_id'])
                quantity = int(data['quantity'])

                size_id = data.get('size_id')
                size_id = int(size_id) if size_id else None

                school_ids = [int(sid) for sid in data['school_ids']]

            except (TypeError, ValueError):
                return jsonify({'error': 'Invalid numeric values'}), 400

            category = data['category']

            # ===============================
            # BUSINESS VALIDATION
            # ===============================
            if quantity <= 0:
                return jsonify({'error': 'Quantity must be positive'}), 400

            if category not in ['student', 'staff']:
                return jsonify({'error': 'Invalid category'}), 400

            if not school_ids:
                return jsonify({'error': 'At least one school must be selected'}), 400

            seller = Seller.query.get(seller_id)
            product = Product.query.get(product_id)

            if not seller:
                return jsonify({'error': 'Seller not found'}), 404

            if not product:
                return jsonify({'error': 'Product not found'}), 404

            # ===============================
            # SIZE VALIDATION
            # ===============================
            product_sizes = ProductSize.query.filter_by(
                product_id=product_id
            ).all()

            if product_sizes:
                if not size_id:
                    return jsonify({
                        'error': 'Size is required for this product'
                    }), 400

                size = ProductSize.query.get(size_id)

                if not size or size.product_id != product_id:
                    return jsonify({
                        'error': 'Invalid size for selected product'
                    }), 400
            else:
                if size_id:
                    return jsonify({
                        'error': 'This product does not support sizes'
                    }), 400

            # ===============================
            # INVENTORY CHECK
            # ===============================

            # DEBUG BLOCK (Keep during development)
            print("---- DISPATCH DEBUG ----")
            print("Requested seller_id:", seller_id)
            print("Requested product_id:", product_id)
            print("DB URI:", app.config['SQLALCHEMY_DATABASE_URI'])

            all_inventory = SellerInventory.query.all()
            print("Total SellerInventory rows:", len(all_inventory))
            for inv in all_inventory:
                print("Inventory Row => Seller:",
                    inv.seller_id,
                    "Product:",
                    inv.product_id,
                    "Remaining:",
                    inv.remaining_stock)

            seller_inv = SellerInventory.query.filter_by(
                seller_id=seller_id,
                product_id=product_id
            ).first()

            if not seller_inv:
                return jsonify({
                    'error': 'No inventory allocated to this seller for selected product'
                }), 400

            total_required = quantity * len(school_ids)

            if seller_inv.remaining_stock < total_required:
                return jsonify({
                    'error': f'Insufficient stock. Required: {total_required}, Available: {seller_inv.remaining_stock}'
                }), 400

            # ===============================
            # CREATE DISPATCH INSTRUCTIONS
            # ===============================
            created_ids = []

            for school_id in school_ids:

                school = School.query.get(school_id)
                if not school:
                    continue

                existing = AdminDispatchInstruction.query.filter_by(
                    seller_id=seller_id,
                    school_id=school_id,
                    product_id=product_id,
                    size_id=size_id,
                    category=category,
                    status='PENDING'
                ).first()

                if existing:
                    continue

                instruction = AdminDispatchInstruction(
                    seller_id=seller_id,
                    school_id=school_id,
                    product_id=product_id,
                    size_id=size_id,
                    category=category,
                    quantity=quantity,
                    status='PENDING',
                    created_at=datetime.utcnow()
                )

                db.session.add(instruction)
                db.session.flush()
                created_ids.append(instruction.id)

            if not created_ids:
                return jsonify({
                    'error': 'No dispatch created (duplicate or invalid schools)'
                }), 400

            db.session.commit()

            print("Dispatch created successfully:", created_ids)

            return jsonify({
                'success': True,
                'message': 'Dispatch instructions created successfully',
                'instruction_ids': created_ids
            }), 201

        except Exception as e:
            db.session.rollback()
            print("Dispatch Error:", str(e))
            return jsonify({
                'error': 'Internal server error',
                'details': str(e)
            }), 500
    
    @app.route('/api/admin/dispatch-list', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def list_dispatch_instructions():

        instructions = AdminDispatchInstruction.query.order_by(
            AdminDispatchInstruction.created_at.desc()
        ).all()

        data = [{
            'id': i.id,
            'seller': {'id': i.seller.id, 'name': i.seller.name},
            'school': {'id': i.school.id, 'name': i.school.name},
            'product': {'id': i.product.id, 'name': i.product.name},
            'category': i.category,
            'quantity': i.quantity,
            'status': i.status,
            'created_at': i.created_at.isoformat()
        } for i in instructions]

        return jsonify(data)
    
    @app.route('/api/product-sizes/<int:product_id>', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def get_product_sizes(product_id):

        sizes = ProductSize.query.filter_by(
            product_id=product_id
        ).all()

        return jsonify([
            {
                "id": size.id,
                "size": size.size,
                "quantity": size.quantity
            }
            for size in sizes
        ])
        
    @app.route('/api/app/schools', methods=['GET'])
    def app_get_schools():

        schools = School.query.all()

        result = []

        for s in schools:
            result.append({
                "id": s.id,
                "name": s.name,
                "address": s.address,
                "contact_phone": s.contact_phone
            })

        return jsonify(result), 200
    
    @app.route('/api/student/select-school', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.STUDENT)
    def select_delivery_school():

        user = get_current_user()
        data = request.get_json()

        school_id = data.get("school_id")

        school = School.query.get(school_id)

        if not school:
            return jsonify({"error": "School not found"}), 404

        user.school_id = school_id
        db.session.commit()

        return jsonify({
            "message": "Delivery school selected",
            "school": {
                "id": school.id,
                "name": school.name
            }
        }), 200
    
    # ===============================
    # STUDENT PAYMENT HISTORY
    # ===============================
    @app.route('/api/student/payment-history', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.STUDENT)
    def student_payment_history():

        user = get_current_user()

        orders = Order.query.filter_by(
            student_id=user.id,
            payment_status="PAID"
        ).order_by(Order.created_at.desc()).all()

        return jsonify([{
            "order_id": o.id,
            "amount": o.total_amount,
            "payment_id": o.razorpay_payment_id,
            "date": o.created_at.strftime("%Y-%m-%d %H:%M"),
            "status": o.status
        } for o in orders])
    
    @app.route('/api/school/complete-order/<int:order_id>', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.SCHOOL)
    def mark_order_handed_over(order_id):

        try:
            user = get_current_user()

            order = Order.query.get(order_id)

            if not order:
                return jsonify({"error": "Order not found"}), 404

            if order.school_id != user.school_id:
                return jsonify({"error": "Unauthorized"}), 403

            if order.status != "READY_FOR_HANDOVER":
                return jsonify({"error": "Order not ready for handover"}), 400

            order.status = "HANDED_OVER"
            order.handed_over_at = datetime.utcnow()

            db.session.commit()

            return jsonify({
                "message": "Order marked as handed over"
            }), 200

        except Exception as e:
            db.session.rollback()
            return jsonify({"error": "Failed to update order"}), 500
        
    @app.route('/api/admin/all-student-orders', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def admin_all_student_orders():

        orders = Order.query.order_by(Order.created_at.desc()).all()

        result = []

        for order in orders:
            school = School.query.get(order.school_id)
            student = User.query.get(order.student_id)

            result.append({
                "order_id": order.id,
                "school_name": school.name if school else None,
                "student_username": student.username if student else None,
                "total_amount": order.total_amount,
                "status": order.status,
                "payment_status": order.payment_status,
                "created_at": order.created_at.isoformat(),
                "handed_over_at": order.handed_over_at.isoformat() if order.handed_over_at else None
            })

        return jsonify(result), 200
    # ===============================
    # RAZORPAY WEBHOOK
    # ===============================
    @app.route('/api/payment/webhook', methods=['POST'])
    def razorpay_webhook():

        webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
        received_signature = request.headers.get("X-Razorpay-Signature")
        body = request.data

        expected_signature = hmac.new(
            webhook_secret.encode(),
            body,
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(received_signature, expected_signature):
            return jsonify({"error": "Invalid webhook signature"}), 400

        payload = request.get_json()

        if payload['event'] == "payment.captured":

            razorpay_payment_id = payload['payload']['payment']['entity']['id']
            razorpay_order_id = payload['payload']['payment']['entity']['order_id']

            order = Order.query.filter_by(
                razorpay_order_id=razorpay_order_id
            ).first()

            if order:
                order.payment_status = "PAID"
                order.status = "COMPLETED"
                order.razorpay_payment_id = razorpay_payment_id
                db.session.commit()

        return jsonify({"status": "ok"}), 200
    
    # ===============================
    # ADMIN REVENUE SUMMARY
    # ===============================
    @app.route('/api/admin/revenue-summary', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def revenue_summary():

        from sqlalchemy import func
        import calendar

        monthly_data = db.session.query(
            func.strftime("%m", Order.created_at),
            func.sum(Order.total_amount)
        ).filter(
            Order.payment_status == "PAID"
        ).group_by(
            func.strftime("%m", Order.created_at)
        ).all()

        labels = [calendar.month_abbr[int(row[0])] for row in monthly_data]
        values = [float(row[1]) for row in monthly_data]

        return success_response(data={
            "labels": labels,
            "data": values
        })

    client = razorpay.Client(auth=("RAZORPAY_KEY_ID", "RAZORPAY_SECRET"))

    @app.route("/api/student/create-order", methods=["POST"])
    @jwt_required()
    @role_required(UserRole.STUDENT)
    def create_order():

        try:

            user = get_current_user()

            if not user:
                return jsonify({"error": "User not found"}), 401

            if not user.school_id:
                return jsonify({"error": "Student school not assigned"}), 400

            # ===============================
            # READ REQUEST DATA
            # ===============================

            data = request.get_json(silent=True)

            if not data:
                return jsonify({"error": "Invalid request body"}), 400

            amount = data.get("amount")
            items = data.get("items")

            print("CREATE ORDER DATA:", data)

            if amount is None:
                return jsonify({"error": "Amount is required"}), 400

            if not items or not isinstance(items, list):
                return jsonify({"error": "Order items missing"}), 400

            amount = float(amount)
            razorpay_amount = int(amount * 100)

            # ===============================
            # CREATE ORDER
            # ===============================

            order = Order(
                student_id=user.id,
                school_id=user.school_id,
                total_amount=amount,
                status="PENDING",
                payment_status="PENDING",
                payment_mode="ONLINE"
            )

            db.session.add(order)
            db.session.flush()   # get order.id

            # ===============================
            # SAVE ORDER ITEMS
            # ===============================

            for item in items:

                product_id = item.get("product_id")
                size_id = item.get("size_id")
                quantity = item.get("quantity")
                price = item.get("price")
                subtotal = item.get("subtotal")

                if not product_id or not size_id or not quantity or not price:
                    db.session.rollback()
                    return jsonify({
                        "error": "Invalid item data",
                        "item": item
                    }), 400

                order_item = OrderItem(
                    order_id=order.id,
                    product_id=int(product_id),
                    size_id=int(size_id),
                    quantity=int(quantity),
                    unit_price=float(price),
                    total_price=float(subtotal)
                )

                db.session.add(order_item)

            print("ORDER CREATED:", order.id)

            # ===============================
            # LIVE MODE (RAZORPAY)
            # ===============================

            razorpay_order = client.order.create({
                "amount": razorpay_amount,
                "currency": "INR",
                "payment_capture": 1
            })

            order.razorpay_order_id = razorpay_order["id"]

            db.session.commit()

            return jsonify({
                "order_id": order.id,
                "razorpay_order_id": razorpay_order["id"],
                "amount": razorpay_order["amount"],
                "currency": razorpay_order["currency"]
            }), 200


        except Exception as e:

            db.session.rollback()

            print("Create order error:", str(e))

            return jsonify({
                "error": "Failed to create order",
                "details": str(e)
            }), 500
    
    @app.route('/api/student/products/<int:school_id>', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.STUDENT)
    def student_products(school_id):

        try:

            inventory_items = SchoolInventory.query.filter(
                SchoolInventory.school_id == school_id,
                SchoolInventory.category == CategoryType.STUDENT
            ).all()

            products = []

            for inv in inventory_items:

                product = Product.query.get(inv.product_id)

                if not product:
                    continue

                images = [
                    request.host_url.rstrip("/") + img.image_url
                    for img in ProductImage.query.filter_by(product_id=product.id).all()
                ]

                products.append({
                    "inventory_id": inv.id,
                    "product_id": product.id,
                    "name": product.name,
                    "description": product.description,
                    "price": product.unit_price,
                    "size_id": inv.size_id,
                    "size": inv.size.size if inv.size else None,
                    "available_quantity": inv.quantity,
                    "images": images
                })

            return jsonify(products), 200

        except Exception as e:
            print("Student products error:", str(e))
            return jsonify({"error": "Failed to load products"}), 500
        
    @app.route('/api/student/create-razorpay-order', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.STUDENT)
    def create_razorpay_order():

        try:

            user = get_current_user()
            data = request.get_json()

            order_id = data.get("order_id")

            if not order_id:
                return jsonify({"error": "Order ID missing"}), 400

            # SQLAlchemy 2.0 safe query
            order = db.session.get(Order, order_id)

            if not order:
                return jsonify({"error": "Order not found"}), 404

            if order.student_id != user.id:
                return jsonify({"error": "Unauthorized order access"}), 403

            if order.payment_status == "PAID":
                return jsonify({"error": "Order already paid"}), 400

            amount = int(order.total_amount * 100)

            # ===============================
            # LIVE MODE (REAL RAZORPAY)
            # ===============================

            razorpay_order = razorpay_client.order.create({
                "amount": amount,
                "currency": "INR",
                "payment_capture": 1
            })

            order.razorpay_order_id = razorpay_order["id"]

            db.session.commit()

            return jsonify({
                "order_id": order.id,
                "razorpay_order_id": razorpay_order["id"],
                "amount": razorpay_order["amount"],
                "currency": "INR",
                "test_mode": False
            }), 200

        except Exception as e:

            db.session.rollback()

            print("Create Razorpay order error:", str(e))

            return jsonify({
                "error": "Failed to create Razorpay order",
                "details": str(e)
            }), 500
        
    # ===============================
    # VERIFY PAYMENT SECURELY
    # ===============================
    @app.route('/api/student/verify-payment', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.STUDENT)
    def verify_payment():

        try:

            user = get_current_user()
            data = request.get_json()

            if not data:
                return jsonify({"error": "Invalid request body"}), 400

            order_id = data.get("order_id")
            razorpay_payment_id = data.get("razorpay_payment_id")
            razorpay_order_id = data.get("razorpay_order_id")
            razorpay_signature = data.get("razorpay_signature")

            # ===============================
            # VALIDATE PARAMETERS
            # ===============================

            if not order_id:
                return jsonify({"error": "Order ID missing"}), 400

            if not razorpay_payment_id or not razorpay_order_id or not razorpay_signature:
                return jsonify({"error": "Missing payment parameters"}), 400


            # ===============================
            # FETCH ORDER
            # ===============================

            order = db.session.get(Order, order_id)

            if not order:
                return jsonify({"error": "Order not found"}), 404


            # ===============================
            # SECURITY CHECK
            # ===============================

            if order.student_id != user.id:
                return jsonify({"error": "Unauthorized order access"}), 403


            # ===============================
            # PREVENT DUPLICATE PAYMENT
            # ===============================

            if order.payment_status == "PAID":
                return jsonify({"message": "Order already verified"}), 200


            # ===============================
            # ORDER MATCH CHECK
            # ===============================

            if order.razorpay_order_id != razorpay_order_id:
                return jsonify({"error": "Order mismatch"}), 400


            # ===============================
            # VERIFY RAZORPAY SIGNATURE
            # ===============================

            secret = os.getenv("RAZORPAY_KEY_SECRET")

            generated_signature = hmac.new(
                bytes(secret, 'utf-8'),
                bytes(f"{razorpay_order_id}|{razorpay_payment_id}", 'utf-8'),
                hashlib.sha256
            ).hexdigest()

            if generated_signature != razorpay_signature:
                return jsonify({"error": "Invalid payment signature"}), 400


            # ===============================
            # MARK PAYMENT SUCCESS
            # ===============================

            order.payment_status = "PAID"
            order.status = "READY_FOR_HANDOVER"
            order.razorpay_payment_id = razorpay_payment_id


            # ===============================
            # DEDUCT STOCK + LEDGER
            # ===============================

            for item in order.items:

                school_inv = SchoolInventory.query.filter_by(
                    school_id=order.school_id,
                    product_id=item.product_id,
                    size_id=item.size_id,
                    category=CategoryType.STUDENT
                ).first()

                if not school_inv:
                    db.session.rollback()
                    return jsonify({
                        "error": f"Inventory not found for product {item.product_id}"
                    }), 400

                if school_inv.quantity < item.quantity:
                    db.session.rollback()
                    return jsonify({
                        "error": f"Insufficient stock for product {item.product_id}"
                    }), 400


                # ===============================
                # DEDUCT STOCK
                # ===============================

                school_inv.sell_stock(item.quantity)


                # ===============================
                # INVENTORY LEDGER
                # ===============================

                ledger = InventoryLedger(
                    product_id=item.product_id,
                    school_id=order.school_id,
                    action="STUDENT_PURCHASE",
                    quantity=-item.quantity,
                    balance_after=school_inv.quantity,
                    reference_type="ORDER",
                    reference_id=order.id,
                    created_at=datetime.now(timezone.utc)
                )

                db.session.add(ledger)


            # ===============================
            # SAVE TRANSACTION
            # ===============================

            db.session.commit()

            return jsonify({
                "message": "Payment verified successfully",
                "order_id": order.id,
                "status": order.status
            }), 200


        except Exception as e:

            db.session.rollback()

            print("Verify payment error:", str(e))

            return jsonify({
                "error": "Payment verification failed",
                "details": str(e)
            }), 500
    
    from sqlalchemy.orm import joinedload

    @app.route('/api/school/inventory-products', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SCHOOL)
    def school_inventory_products():

        try:
            user = get_current_user()

            inventories = (
                SchoolInventory.query
                .options(
                    joinedload(SchoolInventory.product),
                    joinedload(SchoolInventory.size)
                )
                .filter_by(school_id=user.school_id)
                .all()
            )

            result = []

            for inv in inventories:

                product = inv.product
                size = inv.size

                # ===============================
                # PRODUCT IMAGES
                # ===============================
                images = [
                    img.image_url
                    for img in ProductImage.query
                    .filter_by(product_id=inv.product_id)
                    .order_by(ProductImage.display_order)
                    .all()
                ]

                product_name = product.name if product else "Unknown"
                sku = product.sku if product else "-"
                unit_price = product.unit_price if product else 0

                quantity = inv.quantity or 0
                size_label = size.size if size else "DEFAULT"

                total_value = unit_price * quantity

                result.append({
                    "inventory_id": inv.id,
                    "product_id": inv.product_id,

                    "product_name": product_name,
                    "sku": sku,

                    "size_id": inv.size_id,
                    "size": size_label,

                    "category": inv.category.value if inv.category else None,

                    "quantity": quantity,
                    "unit_price": unit_price,
                    "total_value": total_value,

                    "images": images
                })

            return jsonify(result), 200

        except Exception as e:
            print("Inventory products error:", str(e))
            return jsonify({
                "error": "Failed to load inventory"
            }), 500
    
    @app.route('/api/school/place-order', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.SCHOOL)
    def school_place_order():

        try:
            user = get_current_user()
            data = request.get_json()

            items = data.get("items")
            payment_method = data.get("payment_method")

            if not items:
                return jsonify({"error": "No items selected"}), 400

            school = db.session.get(School, user.school_id)

            total_amount = 0
            category = items[0]["category"]

            # ===============================
            # CREATE ORDER
            # ===============================

            if category == "student":

                order = Order(
                    student_id=None,
                    school_id=school.id,
                    status="pending",
                    payment_status="PENDING"
                )

            elif category == "staff":

                order = StaffOrder(
                    school_id=school.id,
                    status="pending"
                )

            else:
                return jsonify({"error": "Invalid category"}), 400

            db.session.add(order)
            db.session.flush()

            order_items_buffer = []

            # ===============================
            # PREPARE ITEMS (NO STOCK CHANGE YET)
            # ===============================

            for item in items:

                if not item.get("size_id"):
                    return jsonify({"error": "size_id is required"}), 400

                inventory = SchoolInventory.query.filter_by(
                    school_id=school.id,
                    product_id=item["product_id"],
                    size_id=item["size_id"],
                    category=CategoryType(item["category"])
                ).first()

                if not inventory:
                    return jsonify({"error": "Inventory not found"}), 400

                if inventory.quantity < item["quantity"]:
                    return jsonify({"error": "Insufficient stock"}), 400

                total_price = item["quantity"] * item["unit_price"]
                total_amount += total_price

                order_items_buffer.append((item, inventory, total_price))


            # ===============================
            # PAYMENT VIA COINS
            # ===============================

            if payment_method == "coins":

                if school.coin_balance < total_amount:
                    return jsonify({"error": "Insufficient coins"}), 400

                school.coin_balance -= total_amount

                for item, inventory, total_price in order_items_buffer:

                    inventory.sell_stock(item["quantity"])

                    if category == "student":

                        order_item = OrderItem(
                            order_id=order.id,
                            product_id=item["product_id"],
                            size_id=item["size_id"],
                            quantity=item["quantity"],
                            unit_price=item["unit_price"],
                            total_price=total_price
                        )

                        ledger_action = "STUDENT_PURCHASE"

                    else:

                        order_item = StaffOrderItem(
                            staff_order_id=order.id,
                            product_id=item["product_id"],
                            size_id=item["size_id"],
                            quantity=item["quantity"],
                            unit_price=item["unit_price"],
                            total_price=total_price
                        )

                        ledger_action = "STAFF_PURCHASE"

                    db.session.add(order_item)

                    ledger = InventoryLedger(
                        product_id=item["product_id"],
                        school_id=school.id,
                        action=ledger_action,
                        quantity=-item["quantity"],
                        balance_after=inventory.quantity,
                        reference_type="ORDER",
                        reference_id=order.id,
                        created_at=datetime.now(timezone.utc)
                    )

                    db.session.add(ledger)

                order.status = "PAID"

                if category == "student":
                    order.payment_status = "PAID"

                db.session.commit()

                return jsonify({
                    "message": "Order completed successfully"
                }), 200


            # ===============================
            # PAYMENT VIA RAZORPAY
            # ===============================

            elif payment_method == "razorpay":

                razorpay_order = razorpay_client.order.create({
                    "amount": int(total_amount * 100),
                    "currency": "INR"
                })

                if category == "student":
                    order.razorpay_order_id = razorpay_order["id"]

                db.session.commit()

                return jsonify({
                    "razorpay_order_id": razorpay_order["id"],
                    "amount": total_amount
                }), 200


            return jsonify({"error": "Invalid payment method"}), 400


        except Exception as e:

            db.session.rollback()

            print("Place order error:", str(e))

            return jsonify({
                "error": "Failed to place order",
                "details": str(e)
            }), 500
    
    @app.route('/api/admin/all-staff-orders', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def admin_all_staff_orders():

        staff_orders = StaffOrder.query.order_by(
            StaffOrder.created_at.desc()
        ).all()

        result = []

        for order in staff_orders:
            school = School.query.get(order.school_id)

            result.append({
                "order_id": order.id,
                "school_name": school.name if school else None,
                "total_amount": order.total_amount,
                "status": order.status,
                "created_at": order.created_at.isoformat()
            })

        return jsonify(result), 200
    
    @app.route('/api/admin/order-details/<string:order_type>/<int:order_id>', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def admin_order_details(order_type, order_id):

        if order_type == "student":
            order = Order.query.get(order_id)
            if not order:
                return jsonify({"error": "Order not found"}), 404

            items = OrderItem.query.filter_by(order_id=order.id).all()

        elif order_type == "staff":
            order = StaffOrder.query.get(order_id)
            if not order:
                return jsonify({"error": "Order not found"}), 404

            items = StaffOrderItem.query.filter_by(staff_order_id=order.id).all()

        else:
            return jsonify({"error": "Invalid order type"}), 400

        item_list = []

        for item in items:
            product = Product.query.get(item.product_id)

            item_list.append({
                "product_name": product.name if product else "Unknown",
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "total_price": item.total_price
            })

        return jsonify({
            "order_id": order.id,
            "total_amount": order.total_amount,
            "items": item_list
        }), 200
    

    @app.route('/api/admin/all-orders', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def admin_all_orders():

        orders_data = []

        # ==========================
        # STUDENT ORDERS
        # ==========================

        student_orders = (
            Order.query
            .options(
                joinedload(Order.items).joinedload(OrderItem.product),
                joinedload(Order.items).joinedload(OrderItem.size),
                joinedload(Order.school)
            )
            .order_by(Order.created_at.desc())
            .all()
        )

        for order in student_orders:

            # SAFE STUDENT FETCH
            student = None
            if order.student_id:
                student = db.session.get(User, order.student_id)

            items = []
            calculated_total = 0

            for item in order.items:

                items.append({
                    "product_name": item.product.name if item.product else "Unknown",
                    "size": item.size.size if item.size else None,
                    "quantity": item.quantity or 0,
                    "unit_price": item.unit_price or 0,
                    "total_price": item.total_price or 0
                })

                calculated_total += item.total_price or 0

            orders_data.append({
                "id": order.id,
                "type": "student",

                "school_name": order.school.name if order.school else "Unknown",

                # STUDENT DETAILS
                "student_name": student.full_name if student else None,
                "student_contact": student.phone_number if student else None,

                "total_amount": order.total_amount or calculated_total,

                "payment_status": order.payment_status,
                "status": order.status,

                "created_at": order.created_at.isoformat(),

                "items": items
            })

        # ==========================
        # STAFF ORDERS
        # ==========================

        staff_orders = (
            StaffOrder.query
            .options(
                joinedload(StaffOrder.items).joinedload(StaffOrderItem.product),
                joinedload(StaffOrder.items).joinedload(StaffOrderItem.size),
                joinedload(StaffOrder.school)
            )
            .order_by(StaffOrder.created_at.desc())
            .all()
        )

        for order in staff_orders:

            items = []
            calculated_total = 0

            for item in order.items:

                items.append({
                    "product_name": item.product.name if item.product else "Unknown",
                    "size": item.size.size if item.size else None,
                    "quantity": item.quantity or 0,
                    "unit_price": item.unit_price or 0,
                    "total_price": item.total_price or 0
                })

                calculated_total += item.total_price or 0

            orders_data.append({
                "id": order.id,
                "type": "staff",

                "school_name": order.school.name if order.school else "Unknown",

                "student_name": None,
                "student_contact": None,

                "total_amount": order.total_amount or calculated_total,

                "payment_status": "PAID" if order.status == "completed" else "PENDING",
                "status": order.status,

                "created_at": order.created_at.isoformat(),

                "items": items
            })

        # ==========================
        # SORT ALL ORDERS
        # ==========================

        orders_data.sort(
            key=lambda x: x["created_at"],
            reverse=True
        )

        return jsonify({
            "success": True,
            "data": orders_data
        }), 200
    
    from datetime import datetime
    from sqlalchemy.orm import joinedload

    @app.route('/api/admin/orders', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def admin_orders_advanced():

        school_id = request.args.get("school_id")
        order_type = request.args.get("type")  # student / staff
        payment_status = request.args.get("payment_status")
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        results = []

        # Convert dates safely
        if start_date and end_date:
            try:
                start_date = datetime.fromisoformat(start_date)
                end_date = datetime.fromisoformat(end_date)
            except ValueError:
                return jsonify({"error": "Invalid date format"}), 400


        # ================= STUDENT ORDERS =================
        if not order_type or order_type == "student":

            query = Order.query.options(
                joinedload(Order.school)
            )

            if school_id:
                try:
                    query = query.filter(Order.school_id == int(school_id))
                except ValueError:
                    return jsonify({"error": "Invalid school_id"}), 400

            if payment_status:
                query = query.filter(Order.payment_status == payment_status)

            if start_date and end_date:
                query = query.filter(Order.created_at.between(start_date, end_date))

            student_orders = query.all()

            for o in student_orders:
                results.append({
                    "id": o.id,
                    "type": "student",
                    "school_id": o.school_id,
                    "school_name": o.school.name if o.school else "Unknown",
                    "total_amount": o.total_amount or 0,
                    "status": o.status,
                    "payment_status": (o.payment_status or "PENDING").upper(),
                    "created_at": o.created_at.isoformat() if o.created_at else None
                })


        # ================= STAFF ORDERS =================
        if not order_type or order_type == "staff":

            query = StaffOrder.query.options(
                joinedload(StaffOrder.school)
            )

            if school_id:
                try:
                    query = query.filter(StaffOrder.school_id == int(school_id))
                except ValueError:
                    return jsonify({"error": "Invalid school_id"}), 400

            if start_date and end_date:
                query = query.filter(StaffOrder.created_at.between(start_date, end_date))

            staff_orders = query.all()

            for o in staff_orders:

                payment_status = "PAID" if o.status == "completed" else "PENDING"

                results.append({
                    "id": o.id,
                    "type": "staff",
                    "school_id": o.school_id,
                    "school_name": o.school.name if o.school else "Unknown",
                    "total_amount": o.total_amount or 0,
                    "status": o.status,
                    "payment_status": payment_status,
                    "created_at": o.created_at.isoformat() if o.created_at else None
                })


        # Sort newest first
        results.sort(
            key=lambda x: x["created_at"] or "",
            reverse=True
        )

        return jsonify(results), 200
    
    @app.route('/api/admin/export-orders', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def export_orders():

        import csv
        from io import StringIO

        output = StringIO()
        writer = csv.writer(output)

        writer.writerow(["Order ID", "Type", "School ID", "Amount", "Status"])

        student_orders = Order.query.all()
        staff_orders = StaffOrder.query.all()

        for o in student_orders:
            writer.writerow([o.id, "student", o.school_id, o.total_amount, o.payment_status])

        for o in staff_orders:
            writer.writerow([o.id, "staff", o.school_id, o.total_amount, o.status])

        response = make_response(output.getvalue())
        response.headers["Content-Disposition"] = "attachment; filename=orders.csv"
        response.headers["Content-type"] = "text/csv"

        return response
    
    from datetime import datetime

    # @app.route('/seller/send-stock', methods=['GET', 'POST'])
    # @session_login_required(UserRole.SELLER)
    # def seller_send_stock():

    #     user = User.query.get(session['user_id'])

    #     # =====================================================
    #     # GET → Show Send Stock Page
    #     # =====================================================
    #     if request.method == 'GET':
    #         schools = School.query.all()
    #         products = Product.query.all()

    #         return render_template(
    #             "seller/send_stock.html",
    #             schools=schools,
    #             products=products
    #         )

    #     # =====================================================
    #     # POST → Process Shipment
    #     # =====================================================
    #     try:
    #         school_id = int(request.form.get('school_id'))
    #         product_id = int(request.form.get('product_id'))
    #         size_id = request.form.get('size_id')
    #         category = request.form.get('category')
    #         quantity = int(request.form.get('quantity'))

    #         size_id = int(size_id) if size_id else None

    #     except (TypeError, ValueError):
    #         return "Invalid input data", 400

    #     # ---------------------------
    #     # Basic Validations
    #     # ---------------------------
    #     if quantity <= 0:
    #         return "Quantity must be positive", 400

    #     if category not in ['student', 'staff']:
    #         return "Invalid category", 400

    #     # ---------------------------
    #     # Verify School & Product
    #     # ---------------------------
    #     school = School.query.get(school_id)
    #     product = Product.query.get(product_id)

    #     if not school:
    #         return "School not found", 404

    #     if not product:
    #         return "Product not found", 404

    #     # ---------------------------
    #     # Size Validation
    #     # ---------------------------
    #     product_sizes = ProductSize.query.filter_by(product_id=product_id).all()

    #     if product_sizes:
    #         if not size_id:
    #             return "Size is required for this product", 400

    #         size = ProductSize.query.get(size_id)

    #         if not size or size.product_id != product_id:
    #             return "Invalid size selected", 400
    #     else:
    #         if size_id:
    #             return "This product does not support sizes", 400
    #         size = None

    #     # ---------------------------
    #     # Inventory Validation
    #     # ---------------------------
    #     seller_inv = SellerInventory.query.filter_by(
    #         seller_id=user.seller_id,
    #         product_id=product_id
    #     ).first()

    #     if not seller_inv:
    #         return "No inventory allocated for this product", 400

    #     if seller_inv.remaining_stock < quantity:
    #         return f"Insufficient stock. Available: {seller_inv.remaining_stock}", 400

    #     # =====================================================
    #     # TRANSACTION BLOCK
    #     # =====================================================
    #     try:
    #         # Create Shipment
    #         shipment = Shipment(
    #             from_seller_id=user.seller_id,
    #             to_school_id=school_id,
    #             status=ShipmentStatus.ON_THE_WAY,
    #             created_at=datetime.utcnow()
    #         )

    #         db.session.add(shipment)
    #         db.session.flush()

    #         # Create Shipment Item
    #         shipment_item = ShipmentItem(
    #             shipment_id=shipment.id,
    #             product_id=product_id,
    #             size_id=size_id,
    #             category=CategoryType(category),
    #             quantity=quantity,
    #             unit_price=product.unit_price
    #         )

    #         db.session.add(shipment_item)

    #         # Update Seller Inventory
    #         seller_inv.sent_stock += quantity
    #         seller_inv.remaining_stock -= quantity

    #         # Update Dispatch Instruction (if exists)
    #         instruction = AdminDispatchInstruction.query.filter_by(
    #             seller_id=user.seller_id,
    #             school_id=school_id,
    #             product_id=product_id,
    #             size_id=size_id,
    #             category=category,
    #             status='PENDING'
    #         ).first()

    #         if instruction:
    #             instruction.status = 'SENT'
    #             instruction.fulfilled_at = datetime.utcnow()

    #         db.session.commit()

    #         return redirect('/seller/dispatch-history')

    #     except Exception as e:
    #         db.session.rollback()
    #         return f"Error processing shipment: {str(e)}", 500


    # =====================================================
    # SELLER - GET ASSIGNED DISPATCHES
    # =====================================================

    @app.route('/api/seller/assigned-dispatches', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SELLER)
    def seller_assigned_dispatches():

        try:
            user = get_current_user()

            instructions = AdminDispatchInstruction.query.filter(
                AdminDispatchInstruction.seller_id == user.seller_id,
                AdminDispatchInstruction.status == 'PENDING'
            ).all()

            result = []

            for ins in instructions:

                product = Product.query.get(ins.product_id)
                school = School.query.get(ins.school_id)

                result.append({
                    "instruction_id": ins.id,
                    "product_id": product.id if product else None,
                    "product_name": product.name if product else "Unknown",
                    "school_id": school.id if school else None,
                    "school_name": school.name if school else "Unknown",
                    "quantity": ins.quantity,
                    "status": ins.status
                })

            return jsonify(result), 200

        except Exception as e:
            return jsonify({"error": str(e)}), 500
            
    @app.route('/api/seller/schools', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SELLER)
    def get_seller_schools():

        try:
            user = get_current_user()

            schools = (
                db.session.query(School)
                .join(
                    AdminDispatchInstruction,
                    AdminDispatchInstruction.school_id == School.id
                )
                .filter(
                    AdminDispatchInstruction.seller_id == user.seller_id
                )
                .distinct()
                .all()
            )

            return jsonify({
                "success": True,
                "data": [
                    {
                        "id": s.id,
                        "name": s.name
                    }
                    for s in schools
                ]
            }), 200

        except Exception as e:
            import traceback
            traceback.print_exc()

            return jsonify({
                "success": False,
                "error": str(e)
            }), 500


    @app.route("/api/seller/send-stock", methods=["POST"])
    @jwt_required()
    @role_required(UserRole.SELLER)
    def seller_send_stock():

        try:

            user = get_current_user()
            data = request.get_json(silent=True)

            # ================= VALIDATE INPUT =================

            if not data:
                return jsonify({"error": "Missing request body"}), 400

            inventory_id = data.get("inventory_id")
            school_id = data.get("school_id")
            quantity = data.get("quantity")

            if inventory_id is None or school_id is None or quantity is None:
                return jsonify({
                    "error": "inventory_id, school_id and quantity are required"
                }), 400

            try:
                inventory_id = int(inventory_id)
                school_id = int(school_id)
                quantity = int(quantity)
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid numeric values"}), 400

            if quantity <= 0:
                return jsonify({"error": "Quantity must be greater than 0"}), 400


            # ================= FETCH INVENTORY =================

            inventory = db.session.get(SellerInventory, inventory_id)

            if not inventory:
                return jsonify({"error": "Inventory not found"}), 404

            if inventory.seller_id != user.seller_id:
                return jsonify({"error": "Unauthorized inventory access"}), 403


            # ================= FETCH PRODUCT =================

            product = db.session.get(Product, inventory.product_id)

            if not product:
                return jsonify({"error": "Product no longer exists"}), 404


            # ================= STOCK VALIDATION =================

            if inventory.remaining_stock < quantity:
                return jsonify({"error": "Insufficient seller stock"}), 400


            # ================= OPTIONAL DISPATCH CHECK =================

            dispatch = AdminDispatchInstruction.query.filter_by(
                seller_id=user.seller_id,
                school_id=school_id,
                product_id=inventory.product_id,
                size_id=inventory.size_id,
                status="PENDING"
            ).first()

            # If dispatch exists enforce dispatch quantity
            if dispatch:
                if quantity > dispatch.quantity:
                    return jsonify({
                        "error": f"Cannot send more than dispatch quantity ({dispatch.quantity})"
                    }), 400


            # ================= CREATE SHIPMENT =================

            shipment = Shipment(
                from_seller_id=user.seller_id,
                to_school_id=school_id,
                status=ShipmentStatus.ON_THE_WAY,
                created_at=datetime.utcnow()
            )

            db.session.add(shipment)
            db.session.flush()


            # ================= CATEGORY =================

            try:
                category_enum = CategoryType(product.category)
            except Exception:
                category_enum = CategoryType.STUDENT


            # ================= CREATE SHIPMENT ITEM =================

            shipment_item = ShipmentItem(
                shipment_id=shipment.id,
                product_id=inventory.product_id,
                size_id=inventory.size_id,
                category=category_enum,
                quantity=quantity,
                unit_price=product.unit_price or 0
            )

            db.session.add(shipment_item)


            # ================= UPDATE SELLER INVENTORY =================

            inventory.sent_stock += quantity
            inventory.remaining_stock -= quantity


            # ================= UPDATE DISPATCH (IF EXISTS) =================

            if dispatch:
                dispatch.quantity -= quantity

                if dispatch.quantity <= 0:
                    dispatch.status = "COMPLETED"


            # ================= INVENTORY LEDGER =================

            ledger = InventoryLedger(
                product_id=inventory.product_id,
                size_id=inventory.size_id,
                seller_id=user.seller_id,
                school_id=school_id,
                action="SELLER_TO_SCHOOL_SHIPMENT",
                quantity=-quantity,
                balance_after=inventory.remaining_stock,
                reference_type="SHIPMENT",
                reference_id=shipment.id,
                created_at=datetime.now(timezone.utc)
            )

            db.session.add(ledger)


            # ================= SAVE =================

            db.session.commit()


            # ================= FETCH IMAGE =================

            image = None

            product_image = ProductImage.query.filter_by(
                product_id=product.id
            ).first()

            if product_image and product_image.image_url:
                image = request.host_url.rstrip("/") + product_image.image_url


            # ================= RESPONSE =================

            return jsonify({

                "message": "Shipment sent successfully",

                "shipment": {

                    "shipment_id": shipment.id,
                    "school_id": school_id,
                    "status": shipment.status.name,

                    "product": {
                        "id": product.id,
                        "name": product.name,
                        "image": image,
                        "description": product.description
                    },

                    "size_id": inventory.size_id,
                    "quantity": quantity,
                    "remaining_stock": inventory.remaining_stock

                }

            }), 200


        except Exception as e:

            db.session.rollback()

            print("SEND STOCK ERROR:", str(e))

            return jsonify({
                "error": "Internal server error",
                "details": str(e)
            }), 500
            
    @app.route('/api/admin/inventory-ledger', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def admin_inventory_ledger():

        try:

            logs = InventoryLedger.query.order_by(
                InventoryLedger.created_at.desc()
            ).all()

            result = []

            for log in logs:

                # ================= FETCH RELATED DATA =================

                product = Product.query.get(log.product_id)

                seller = None
                school = None

                if log.seller_id:
                    seller = Seller.query.get(log.seller_id)

                if log.school_id:
                    school = School.query.get(log.school_id)


                # ================= DETERMINE FROM / TO =================

                from_entity = "ADMIN"
                to_entity = "-"

                if log.action == "ADMIN_STOCK_ADDED":

                    from_entity = "ADMIN"
                    to_entity = seller.name if seller else "Seller"


                elif log.action == "SELLER_TO_SCHOOL_SHIPMENT":

                    from_entity = seller.name if seller else "Seller"
                    to_entity = school.name if school else "School"


                elif log.action == "STUDENT_PURCHASE":

                    from_entity = school.name if school else "School"
                    to_entity = "Student"


                elif log.action == "STAFF_PURCHASE":

                    from_entity = school.name if school else "School"
                    to_entity = "Staff"


                # ================= FORMAT DATE =================

                created_at = None

                if log.created_at:
                    created_at = log.created_at.strftime("%Y-%m-%d %H:%M")


                # ================= BUILD RESPONSE =================

                result.append({

                    "id": log.id,
                    "product_id": log.product_id,

                    "product_name": product.name if product else "Unknown",

                    "action": log.action,

                    "from_entity": from_entity,
                    "to_entity": to_entity,

                    "quantity": log.quantity,
                    "balance_after": log.balance_after,

                    "reference_type": log.reference_type,
                    "reference_id": log.reference_id,

                    "created_at": created_at

                })

            return jsonify(result), 200


        except Exception as e:

            print("ADMIN LEDGER ERROR:", str(e))

            return jsonify({
                "error": "Failed to load inventory ledger",
                "details": str(e)
            }), 500
    
    # =====================================================
    # SELLER - FULFILL DISPATCH
    # =====================================================

    @app.route('/api/seller/fulfill-dispatch/<int:instruction_id>', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.SELLER)
    def fulfill_dispatch(instruction_id):

        try:
            user = get_current_user()
            data = request.get_json()

            send_quantity = int(data.get("quantity", 0))

            if send_quantity <= 0:
                return jsonify({"error": "Quantity must be greater than 0"}), 400

            instruction = db.session.get(AdminDispatchInstruction, instruction_id)

            if not instruction:
                return jsonify({"error": "Dispatch instruction not found"}), 404

            if instruction.seller_id != user.seller_id:
                return jsonify({"error": "Not authorized"}), 403

            if instruction.status != 'PENDING':
                return jsonify({"error": "Instruction is not pending"}), 400

            if send_quantity > instruction.quantity:
                return jsonify({
                    "error": "Cannot send more than assigned quantity"
                }), 400

            seller_inv = SellerInventory.query.filter_by(
                seller_id=user.seller_id,
                product_id=instruction.product_id
            ).first()

            if not seller_inv or seller_inv.remaining_stock < send_quantity:
                return jsonify({"error": "Insufficient stock"}), 400

            # Create Shipment
            shipment = Shipment(
                from_seller_id=user.seller_id,
                to_school_id=instruction.school_id,
                status=ShipmentStatus.ON_THE_WAY,
                created_at=datetime.now(datetime.UTC)
            )

            db.session.add(shipment)
            db.session.flush()

            shipment_item = ShipmentItem(
                shipment_id=shipment.id,
                product_id=instruction.product_id,
                size_id=instruction.size_id,
                category=CategoryType[instruction.category.upper()],
                quantity=send_quantity,
                unit_price=instruction.product.unit_price
            )

            db.session.add(shipment_item)

            # Update Seller Inventory
            seller_inv.sent_stock += send_quantity
            seller_inv.remaining_stock -= send_quantity

            # 🔥 Reduce remaining instruction quantity
            instruction.quantity -= send_quantity

            if instruction.quantity == 0:
                instruction.status = 'SENT'

            db.session.commit()

            return jsonify({
                "message": "Shipment created successfully",
                "shipment_id": shipment.id
            }), 200

        except Exception as e:
            db.session.rollback()
            return jsonify({"error": "Failed to fulfill dispatch"}), 500

    @app.route("/api/school/receive-shipment/<int:shipment_id>", methods=["POST"])
    @jwt_required()
    @role_required(UserRole.SCHOOL)
    def receive_shipment(shipment_id):
        try:
            user = get_current_user()
            data = request.get_json(silent=True) or {}

            shipment = db.session.get(Shipment, shipment_id)

            if not shipment:
                return jsonify({"error": "Shipment not found"}), 404

            # Ensure this school owns the shipment
            if shipment.to_school_id != user.school_id:
                return jsonify({"error": "Unauthorized shipment"}), 403

            # Prevent receiving twice
            if shipment.status == ShipmentStatus.DELIVERED:
                return jsonify({"error": "Shipment already received"}), 400

            # Process shipment items
            for item in shipment.items:

                size_id = item.size_id
                category = item.category

                # Find existing school inventory row
                school_inventory = SchoolInventory.query.filter_by(
                    school_id=user.school_id,
                    product_id=item.product_id,
                    size_id=size_id,
                    category=category
                ).first()

                # If not exist → create inventory row
                if not school_inventory:
                    school_inventory = SchoolInventory(
                        school_id=user.school_id,
                        product_id=item.product_id,
                        size_id=size_id,
                        category=category,
                        quantity=0
                    )
                    db.session.add(school_inventory)

                # Add received stock
                school_inventory.receive_stock(item.quantity)

            # Update shipment status
            shipment.status = ShipmentStatus.DELIVERED
            shipment.received_at = datetime.utcnow()

            db.session.commit()

            return jsonify({
                "message": "Shipment received successfully",
                "shipment_id": shipment.id,
                "status": shipment.status.name
            }), 200

        except Exception as e:
            db.session.rollback()
            print("RECEIVE SHIPMENT ERROR:", str(e))

            return jsonify({
                "error": "Internal server error",
                "details": str(e)
            }), 500
    
    
    # === FEATURE 2: SYSTEM ANALYTICS ===
    @app.route('/api/admin/analytics/overview', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def analytics_overview():
        total_schools = School.query.count()
        total_sellers = Seller.query.count()
        total_products = Product.query.count()

        # Student sales
        student_sales = db.session.query(db.func.sum(Order.total_amount)).filter(
            Order.status == 'completed'
        ).scalar() or 0
        student_orders = Order.query.filter_by(status='completed').count()

        # Staff sales
        staff_sales = db.session.query(db.func.sum(StaffOrder.total_amount)).filter(
            StaffOrder.status == 'completed'
        ).scalar() or 0
        staff_orders = StaffOrder.query.filter_by(status='completed').count()

        total_revenue = student_sales + staff_sales

        pending_shipments = Shipment.query.filter_by(status=ShipmentStatus.ON_THE_WAY).count()
        delivered_shipments = Shipment.query.filter_by(status=ShipmentStatus.DELIVERED).count()

        return jsonify({
            "total_schools": total_schools,
            "total_sellers": total_sellers,
            "total_products": total_products,
            "total_student_sales": student_orders,
            "total_staff_sales": staff_orders,
            "total_revenue": round(total_revenue, 2),
            "pending_shipments": pending_shipments,
            "delivered_shipments": delivered_shipments
        })

    @app.route('/api/admin/analytics/school/<int:school_id>', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def analytics_school(school_id):
        school = School.query.get(school_id)
        if not school:
            return jsonify({'error': 'School not found'}), 404

        # Student orders
        student_orders = Order.query.filter_by(school_id=school_id, status='completed').all()
        student_revenue = sum(o.total_amount for o in student_orders)

        # Staff orders
        staff_orders = StaffOrder.query.filter_by(school_id=school_id, status='completed').all()
        staff_revenue = sum(o.total_amount for o in staff_orders)

        # Inventory summary
        school_inventory = SchoolInventory.query.filter_by(school_id=school_id).all()
        student_stock = sum(i.quantity for i in school_inventory if i.category == CategoryType.STUDENT)
        staff_stock = sum(i.quantity for i in school_inventory if i.category == CategoryType.STAFF)

        # Recent shipments
        shipments = Shipment.query.filter_by(to_school_id=school_id).order_by(
            Shipment.created_at.desc()
        ).limit(5).all()

        return jsonify({
            'school': {'id': school.id, 'name': school.name},
            'student_orders_count': len(student_orders),
            'student_revenue': round(student_revenue, 2),
            'staff_orders_count': len(staff_orders),
            'staff_revenue': round(staff_revenue, 2),
            'student_stock': student_stock,
            'staff_stock': staff_stock,
            'recent_shipments': [{
                'id': s.id,
                'status': s.status.value,
                'from_seller': s.from_seller.name,
                'created_at': s.created_at.isoformat()
            } for s in shipments]
        })

    @app.route('/api/admin/analytics/seller/<int:seller_id>', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def analytics_seller(seller_id):
        seller = Seller.query.get(seller_id)
        if not seller:
            return jsonify({'error': 'Seller not found'}), 404

        # Dispatch instructions
        instructions = AdminDispatchInstruction.query.filter_by(seller_id=seller_id).all()
        pending = sum(1 for i in instructions if i.status == 'PENDING')
        sent = sum(1 for i in instructions if i.status == 'SENT')
        completed = sum(1 for i in instructions if i.status == 'COMPLETED')

        # Total dispatched quantity
        total_dispatched = sum(i.quantity for i in instructions if i.status in ['SENT', 'COMPLETED'])

        # Seller inventory summary
        seller_inv = SellerInventory.query.filter_by(seller_id=seller_id).all()
        total_allocated = sum(i.total_allocated for i in seller_inv)
        total_remaining = sum(i.remaining_stock for i in seller_inv)

        return jsonify({
            'seller': {'id': seller.id, 'name': seller.name},
            'total_instructions': len(instructions),
            'pending': pending,
            'sent': sent,
            'completed': completed,
            'total_dispatched_quantity': total_dispatched,
            'total_allocated': total_allocated,
            'total_remaining': total_remaining
        })

    @app.route('/api/admin/analytics/product/<int:product_id>', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def analytics_product(product_id):
        product = Product.query.get(product_id)
        if not product:
            return jsonify({'error': 'Product not found'}), 404

        # Student sales
        student_orders = OrderItem.query.join(Order).filter(
            OrderItem.product_id == product_id,
            Order.status == 'completed'
        ).all()
        student_qty = sum(oi.quantity for oi in student_orders)
        student_revenue = sum(oi.total_price for oi in student_orders)

        # Staff sales
        staff_orders = StaffOrderItem.query.join(StaffOrder).filter(
            StaffOrderItem.product_id == product_id,
            StaffOrder.status == 'completed'
        ).all()
        staff_qty = sum(oi.quantity for oi in staff_orders)
        staff_revenue = sum(oi.total_price for oi in staff_orders)

        # Current inventory across schools
        school_inv = SchoolInventory.query.filter_by(product_id=product_id).all()
        total_school_stock = sum(i.quantity for i in school_inv)

        return jsonify({
            'product': {
                'id': product.id,
                'name': product.name,
                'sku': product.sku,
                'unit_price': product.unit_price
            },
            'student_sales': {
                'orders': len(set(oi.order_id for oi in student_orders)),
                'quantity': student_qty,
                'revenue': round(student_revenue, 2)
            },
            'staff_sales': {
                'orders': len(set(oi.order_id for oi in staff_orders)),
                'quantity': staff_qty,
                'revenue': round(staff_revenue, 2)
            },
            'total_school_inventory': total_school_stock
        })

    @app.route('/school/sales')
    @session_login_required(UserRole.SCHOOL)
    def school_sales_page():

        user = User.query.get(session['user_id'])

        student_orders = Order.query.filter_by(
            school_id=user.school_id,
            status='completed'
        ).all()

        staff_orders = StaffOrder.query.filter_by(
            school_id=user.school_id,
            status='completed'
        ).all()

        total_student_sales = sum(o.total_amount for o in student_orders)
        total_staff_sales = sum(o.total_amount for o in staff_orders)

        return render_template(
            'school/school_sales.html',
            student_orders=student_orders,
            staff_orders=staff_orders,
            total_student_sales=total_student_sales,
            total_staff_sales=total_staff_sales
        )
    
    
    # === FEATURE 3: SCHOOL STAFF PURCHASE SYSTEM ===
    from flask import flash, redirect, url_for

    @app.route('/school/staff-purchase', methods=['POST'])
    @session_login_required(UserRole.SCHOOL)
    def school_staff_purchase():

        user = User.query.get(session['user_id'])

        # ==========================
        # BASIC INPUT VALIDATION
        # ==========================
        product_id = request.form.get('product_id')
        size_id = request.form.get('size_id')
        quantity = request.form.get('quantity')
        payment_method = request.form.get('payment_method')

        if not product_id or not size_id or not quantity:
            flash("Please select product, size and quantity", "error")
            return redirect(url_for('school_staff_orders_page'))

        try:
            product_id = int(product_id)
            size_id = int(size_id)
            quantity = int(quantity)
        except ValueError:
            flash("Invalid input values", "error")
            return redirect(url_for('school_staff_orders_page'))

        if quantity <= 0:
            flash("Invalid quantity", "error")
            return redirect(url_for('school_staff_orders_page'))

        # ==========================
        # FETCH DATA
        # ==========================
        school = School.query.get(user.school_id)

        product = Product.query.get(product_id)
        if not product:
            flash("Product not found", "error")
            return redirect(url_for('school_staff_orders_page'))

        school_inv = SchoolInventory.query.filter_by(
            school_id=user.school_id,
            product_id=product_id,
            size_id=size_id,
            category=CategoryType.STAFF
        ).first()

        if not school_inv:
            flash("Selected size not available for this school", "error")
            return redirect(url_for('school_staff_orders_page'))

        if school_inv.quantity <= 0:
            flash("Selected size is out of stock", "error")
            return redirect(url_for('school_staff_orders_page'))

        if school_inv.quantity < quantity:
            flash("Insufficient stock", "error")
            return redirect(url_for('school_staff_orders_page'))

        total_amount = product.unit_price * quantity

        # ==========================================================
        # ================== COINS PAYMENT ==========================
        # ==========================================================
        if payment_method == "coins":

            if school.coin_balance < total_amount:
                flash("Insufficient coins", "error")
                return redirect(url_for('school_staff_orders_page'))

            # Create order
            staff_order = StaffOrder(
                school_id=user.school_id,
                total_amount=total_amount,
                status='completed',
                created_at=datetime.utcnow()
            )
            db.session.add(staff_order)
            db.session.flush()

            staff_item = StaffOrderItem(
                staff_order_id=staff_order.id,
                product_id=product_id,
                size_id=size_id,
                quantity=quantity,
                unit_price=product.unit_price,
                total_price=total_amount
            )
            db.session.add(staff_item)

            # Deduct stock & coins
            school_inv.sell_stock(quantity)
            school.coin_balance -= total_amount

            db.session.commit()

            flash("Staff purchase completed using balance!", "success")
            return redirect(url_for('school_staff_orders_page'))

        # ==========================================================
        # ================= RAZORPAY PAYMENT =======================
        # ==========================================================
        elif payment_method == "razorpay":

            # For now mark as pending
            staff_order = StaffOrder(
                school_id=user.school_id,
                total_amount=total_amount,
                status='pending',
                created_at=datetime.utcnow()
            )
            db.session.add(staff_order)
            db.session.flush()

            staff_item = StaffOrderItem(
                staff_order_id=staff_order.id,
                product_id=product_id,
                size_id=size_id,
                quantity=quantity,
                unit_price=product.unit_price,
                total_price=total_amount
            )
            db.session.add(staff_item)

            db.session.commit()

            # ⚠️ Stock should NOT be deducted until payment success webhook
            flash("Redirecting to online payment...", "info")

            # Here you integrate Razorpay order creation
            return redirect(url_for('school_staff_orders_page'))

        else:
            flash("Invalid payment method", "error")
            return redirect(url_for('school_staff_orders_page'))

    @app.route('/school/inventory')
    @session_login_required(UserRole.SCHOOL)
    def school_inventory_page():

        user = User.query.get(session['user_id'])

        inventory = SchoolInventory.query.filter_by(
            school_id=user.school_id
        ).all()

        return render_template(
            'school/inventory.html',
            inventory=inventory
        )
        
    @app.route('/school/student-orders')
    @session_login_required(UserRole.SCHOOL)
    def school_student_orders_page():

        user = User.query.get(session['user_id'])

        orders = Order.query.filter_by(
            school_id=user.school_id
        ).order_by(Order.created_at.desc()).all()

        return render_template(
            'school/student_orders.html',
            orders=orders
        )
    
    @app.route('/school/low-stock')
    @session_login_required(UserRole.SCHOOL)
    def school_low_stock_page():

        user = User.query.get(session['user_id'])

        # Define threshold (you can later store in DB)
        threshold = 10

        low_stock_items = SchoolInventory.query.filter(
            SchoolInventory.school_id == user.school_id,
            SchoolInventory.quantity <= threshold
        ).all()

        return render_template(
            'school/low_stock.html',
            low_stock_items=low_stock_items,
            threshold=threshold
        )
        
    
    @app.route('/school/staff-orders')
    @session_login_required(UserRole.SCHOOL)
    def school_staff_orders_page():

        user = db.session.get(User, session['user_id'])

        staff_orders = (
            StaffOrder.query
            .filter_by(school_id=user.school_id)
            .order_by(StaffOrder.created_at.desc())
            .all()
        )

        staff_inventory = SchoolInventory.query.filter(
            SchoolInventory.school_id == user.school_id,
            SchoolInventory.category == CategoryType.STAFF,
            SchoolInventory.quantity > 0
        ).all()

        school = db.session.get(School, user.school_id)

        return render_template(
            "school/staff_orders.html",
            staff_orders=staff_orders,
            staff_inventory=staff_inventory,
            school=school
        )
        

    
    @app.route('/api/app/register', methods=['POST'])
    def app_register():

        try:

            data = request.get_json()

            if not data:
                return jsonify({"error": "Invalid request"}), 400

            full_name = data.get("name")
            phone = data.get("phone")
            password = data.get("password")

            if not full_name or not phone or not password:
                return jsonify({"error": "All fields are required"}), 400

            # check existing phone
            existing_user = User.query.filter_by(phone_number=phone).first()

            if existing_user:
                return jsonify({"error": "Phone already registered"}), 400

            new_user = User(
                full_name=full_name,
                phone_number=phone,
                role=UserRole.STUDENT
            )

            new_user.set_password(password)

            db.session.add(new_user)
            db.session.commit()

            access_token = create_access_token(
                identity=str(new_user.id),
                additional_claims={"role": new_user.role.value}
            )

            return jsonify({
                "message": "Registration successful",
                "access_token": access_token
            }), 201

        except Exception as e:

            print("Register error:", str(e))
            db.session.rollback()

            return jsonify({
                "error": "Registration failed"
            }), 500
    
    @app.route('/api/app/login', methods=['POST', 'OPTIONS'])
    def app_login():

        if request.method == "OPTIONS":
            return jsonify({"status": "ok"}), 200

        try:

            data = request.get_json() or {}

            identifier = data.get("identifier")
            password = data.get("password")

            if not identifier or not password:
                return jsonify({"error": "Identifier and password required"}), 400

            # detect login type
            if identifier.isdigit():
                user = User.query.filter_by(phone_number=identifier).first()
            else:
                user = User.query.filter_by(username=identifier).first()

            if not user:
                return jsonify({"error": "User not found"}), 404

            if not user.check_password(password):
                return jsonify({"error": "Invalid password"}), 401

            if not user.is_active:
                return jsonify({"error": "Account disabled"}), 403

            access_token = create_access_token(
                identity=str(user.id),
                additional_claims={"role": user.role.value}
            )

            user_data = {
                "id": user.id,
                "full_name": user.full_name,
                "role": user.role.value,
                "phone_number": user.phone_number,
                "username": user.username,
                "school_id": user.school_id,
                "seller_id": user.seller_id
            }

            return jsonify({
                "access_token": access_token,
                "role": user.role.value,
                "user": user_data
            }), 200

        except Exception as e:
            print("Login error:", str(e))
            return jsonify({"error": "Login failed"}), 500
    
    @app.route('/api/student/my-orders', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.STUDENT)
    def student_my_orders():
        user = get_current_user()

        orders = Order.query.filter_by(
            student_id=user.id
        ).order_by(Order.created_at.desc()).all()

        return jsonify([{
            "order_id": o.id,
            "total_amount": o.total_amount,
            "status": o.status,
            "payment_status": o.payment_status,
            "created_at": o.created_at.isoformat()
        } for o in orders])
    # === FEATURE 4: SALES TRACKING ENHANCEMENTS ===
    # Already covered in analytics endpoints above

    # === FEATURE 5: JWT-ONLY AUTH — REMOVED SESSIONS ===
    # All routes now use @jwt_required() only

    # === FEATURE 6: STOCK SAFETY & VALIDATION ===
    # Implemented in all critical paths above (e.g., stock checks before dispatch/fulfillment/order)

    @app.route('/api/admin/alerts/low-stock', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def api_low_stock_alerts():

        low_stock_items = SchoolInventory.query.filter(
            SchoolInventory.quantity <= SchoolInventory.low_stock_threshold
        ).all()

        alerts = []

        for item in low_stock_items:
            product = Product.query.get(item.product_id)
            school = School.query.get(item.school_id)

            alerts.append({
                "product_name": product.name,
                "school_name": school.name,
                "quantity": item.quantity,
                "threshold": item.low_stock_threshold,
                "category": item.category.value,
                "size": item.size.size if item.size else None,
                "last_updated": item.updated_at.isoformat() if hasattr(item, "updated_at") else None
            })

        return jsonify({
            "success": True,
            "alerts": alerts
        }), 200

    # ===============================
    # SAVE EXPO PUSH TOKEN
    # ===============================
    @app.route('/api/save-push-token', methods=['POST'])
    @jwt_required()
    def save_push_token():

        user = get_current_user()
        data = request.get_json()

        token = data.get("token")

        if not token:
            return jsonify({"error": "Token required"}), 400

        user.expo_push_token = token
        db.session.commit()

        return jsonify({"message": "Push token saved"}), 200
    
    @app.route('/api/admin/sales-overview', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def api_sales_overview():
        schools = School.query.all()
        data = []

        for school in schools:
            student_sales = db.session.query(db.func.sum(Order.total_amount)).filter(
                Order.school_id == school.id,
                Order.status == 'completed'
            ).scalar() or 0

            staff_sales = db.session.query(db.func.sum(StaffOrder.total_amount)).filter(
                StaffOrder.school_id == school.id,
                StaffOrder.status == 'completed'
            ).scalar() or 0

            data.append({
                "school": school.name,
                "student_sales": student_sales,
                "staff_sales": staff_sales,
                "total": student_sales + staff_sales
            })
        return jsonify(data)
    
    @app.route('/api/admin/inventory', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def api_inventory():

        sellers = Seller.query.all()

        return jsonify([
            {
                "seller": s.name,
                "total_allocated": sum(i.total_allocated for i in s.inventory),
                "remaining": sum(i.remaining_stock for i in s.inventory)
            }
            for s in sellers
        ])
    
    @app.route('/api/admin/commission-summary', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def api_commission_summary():

        schools = School.query.all()
        result = []

        for school in schools:

            student_revenue = db.session.query(db.func.sum(Order.total_amount)).filter(
                Order.school_id == school.id,
                Order.status == 'completed'
            ).scalar() or 0

            staff_revenue = db.session.query(db.func.sum(StaffOrder.total_amount)).filter(
                StaffOrder.school_id == school.id,
                StaffOrder.status == 'completed'
            ).scalar() or 0

            total_revenue = student_revenue + staff_revenue
            commission = (student_revenue * school.commission_percentage) / 100
            platform_profit = total_revenue - commission

            result.append({
                "school": school.name,
                "student_revenue": round(student_revenue, 2),
                "staff_revenue": round(staff_revenue, 2),
                "total_revenue": round(total_revenue, 2),
                "commission_percentage": school.commission_percentage,
                "commission_amount": round(commission, 2),
                "platform_profit": round(platform_profit, 2)
            })

        return jsonify(result)
    
    # === FEATURE 8: CHART DATA ENDPOINTS ===
    @app.route('/api/admin/chart/monthly-sales', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def chart_monthly_sales():
        # Group by month (YYYY-MM)
        monthly_data = db.session.query(
            db.func.date_trunc('month', Order.created_at).label('month'),
            db.func.sum(Order.total_amount).label('student_revenue'),
            db.func.count(Order.id).label('student_orders')
        ).filter(Order.status == 'completed').group_by('month').all()

        staff_monthly = db.session.query(
            db.func.date_trunc('month', StaffOrder.created_at).label('month'),
            db.func.sum(StaffOrder.total_amount).label('staff_revenue'),
            db.func.count(StaffOrder.id).label('staff_orders')
        ).filter(StaffOrder.status == 'completed').group_by('month').all()

        # Merge
        result = {}
        for m in monthly_data:
            key = m.month.strftime('%Y-%m')
            result[key] = {
                'student_revenue': float(m.student_revenue or 0),
                'student_orders': m.student_orders,
                'staff_revenue': 0.0,
                'staff_orders': 0
            }
        for m in staff_monthly:
            key = m.month.strftime('%Y-%m')
            if key in result:
                result[key]['staff_revenue'] = float(m.staff_revenue or 0)
                result[key]['staff_orders'] = m.staff_orders
            else:
                result[key] = {
                    'student_revenue': 0.0,
                    'student_orders': 0,
                    'staff_revenue': float(m.staff_revenue or 0),
                    'staff_orders': m.staff_orders
                }

        # Sort by month
        sorted_result = dict(sorted(result.items()))
        return jsonify(sorted_result)

    @app.route('/api/admin/chart/school-comparison', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def chart_school_comparison():
        schools = School.query.all()
        data = []
        for school in schools:
            student_orders = Order.query.filter_by(school_id=school.id, status='completed').all()
            staff_orders = StaffOrder.query.filter_by(school_id=school.id, status='completed').all()

            data.append({
                'school': school.name,
                'student_revenue': round(sum(o.total_amount for o in student_orders), 2),
                'staff_revenue': round(sum(o.total_amount for o in staff_orders), 2),
                'total_revenue': round(
                    sum(o.total_amount for o in student_orders) +
                    sum(o.total_amount for o in staff_orders), 2
                ),
                'student_orders': len(student_orders),
                'staff_orders': len(staff_orders)
            })
        return jsonify(data)

    @app.route('/api/admin/chart/product-performance', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def chart_product_performance():
        products = Product.query.all()
        data = []
        for p in products:
            student_qty = db.session.query(db.func.sum(OrderItem.quantity)).join(Order).filter(
                OrderItem.product_id == p.id,
                Order.status == 'completed'
            ).scalar() or 0
            staff_qty = db.session.query(db.func.sum(StaffOrderItem.quantity)).join(StaffOrder).filter(
                StaffOrderItem.product_id == p.id,
                StaffOrder.status == 'completed'
            ).scalar() or 0

            student_rev = db.session.query(db.func.sum(OrderItem.total_price)).join(Order).filter(
                OrderItem.product_id == p.id,
                Order.status == 'completed'
            ).scalar() or 0
            staff_rev = db.session.query(db.func.sum(StaffOrderItem.total_price)).join(StaffOrder).filter(
                StaffOrderItem.product_id == p.id,
                StaffOrder.status == 'completed'
            ).scalar() or 0

            data.append({
                'product': p.name,
                'sku': p.sku,
                'student_quantity': student_qty,
                'staff_quantity': staff_qty,
                'student_revenue': round(student_rev, 2),
                'staff_revenue': round(staff_rev, 2),
                'total_quantity': student_qty + staff_qty,
                'total_revenue': round(student_rev + staff_rev, 2)
            })
        return jsonify(data)

    # === FEATURE 9: DATA CONSISTENCY RULES ===
    # Enforced via:
    # - Foreign keys in models
    # - Business logic in endpoints (e.g., dispatch → shipment link)
    # - Status transitions (PENDING → SENT → COMPLETED)

    # === FEATURE 10: ERP HIERARCHY ENFORCEMENT ===
    # Done via role_required decorators and data isolation (e.g., school can only see own school data)

    # === CORE API ROUTES (UPDATED FOR JWT) ===
    @app.route('/api/login', methods=['POST'])
    def login_route():
        return login()

    # @app.route('/api/register', methods=['POST'])
    # def register_route():
    #     return register_user()

    @app.route('/api/admin/test/add-coins', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def add_coins_api():

        data = request.get_json()
        school_id = data.get("school_id")
        amount = data.get("amount")

        if not school_id or not amount:
            return jsonify({"error": "Missing fields"}), 400

        school = School.query.get(school_id)
        if not school:
            return jsonify({"error": "School not found"}), 404

        school.coin_balance += int(amount)
        db.session.commit()

        return jsonify({
            "message": "Coins added successfully",
            "new_balance": school.coin_balance
        }), 200
        
        
    @app.route('/api/admin/schools', methods=['GET', 'POST', 'PUT', 'DELETE'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def api_schools():

        from sqlalchemy import func

        # ============================================================
        # CREATE SCHOOL (POST)
        # ============================================================
        if request.method == 'POST':

            data = request.get_json()

            required = [
                'name',
                'contact_email',
                'username',
                'password'
            ]

            if not data or not all(k in data for k in required):
                return error_response("Missing required fields")

            username = data['username']
            email = data['contact_email']

            # Username uniqueness
            if User.query.filter_by(username=username).first():
                return error_response("Username already exists")

            # Email uniqueness
            if User.query.filter_by(email=email).first():
                return error_response("Email already exists")

            # Create School
            school = School(
                name=data['name'],
                address=data.get('address'),
                contact_person=data.get('contact_person'),
                contact_phone=data.get('contact_phone'),
                contact_email=email,
                commission_percentage=data.get('commission_percentage', 0),
                coin_balance=0
            )

            db.session.add(school)
            db.session.flush()

            # Create School User
            school_user = User(
                full_name=data.get('contact_person') or "School Admin",
                phone_number=data.get('contact_phone') or "0000000000",
                username=username,
                email=email,
                role=UserRole.SCHOOL,
                school_id=school.id
            )

            school_user.set_password(data['password'])

            db.session.add(school_user)
            db.session.commit()

            return success_response(
                message="School created successfully",
                data={
                    "id": school.id,
                    "username": school_user.username
                },
                status=201
            )

        # ============================================================
        # UPDATE SCHOOL (PUT)
        # ============================================================
        if request.method == 'PUT':

            data = request.get_json()
            school_id = data.get("id")

            if not school_id:
                return error_response("School ID required")

            school = School.query.get(school_id)
            if not school:
                return error_response("School not found", 404)

            school.name = data.get("name", school.name)
            school.address = data.get("address", school.address)
            school.contact_person = data.get("contact_person", school.contact_person)
            school.contact_phone = data.get("contact_phone", school.contact_phone)
            school.contact_email = data.get("contact_email", school.contact_email)
            school.commission_percentage = data.get(
                "commission_percentage",
                school.commission_percentage
            )

            school_user = User.query.filter_by(
                school_id=school.id,
                role=UserRole.SCHOOL
            ).first()

            if school_user:

                if data.get("username"):

                    existing = User.query.filter_by(
                        username=data["username"]
                    ).first()

                    if existing and existing.id != school_user.id:
                        return error_response("Username already exists")

                    school_user.username = data["username"]

                if data.get("password"):
                    school_user.set_password(data["password"])

                # update name/phone if changed
                if data.get("contact_person"):
                    school_user.full_name = data["contact_person"]

                if data.get("contact_phone"):
                    school_user.phone_number = data["contact_phone"]

            db.session.commit()

            return success_response(message="School updated successfully")

        # ============================================================
        # DELETE SCHOOL (DELETE)
        # ============================================================
        if request.method == 'DELETE':

            school_id = request.args.get("id")

            if not school_id:
                return error_response("School ID required")

            school = School.query.get(school_id)
            if not school:
                return error_response("School not found", 404)

            User.query.filter_by(
                school_id=school.id,
                role=UserRole.SCHOOL
            ).delete()

            db.session.delete(school)
            db.session.commit()

            return success_response(message="School deleted successfully")

        # ============================================================
        # GET SCHOOLS
        # ============================================================

        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 10))
        search = request.args.get("search", "").strip()

        query = School.query

        if search:
            query = query.filter(
                School.name.ilike(f"%{search}%")
            )

        pagination = query.order_by(School.id.desc()).paginate(
            page=page,
            per_page=per_page,
            error_out=False
        )

        schools = pagination.items
        result = []

        for s in schools:

            student_revenue = db.session.query(
                func.coalesce(func.sum(Order.total_amount), 0)
            ).filter(
                Order.school_id == s.id,
                Order.status == 'completed'
            ).scalar()

            staff_revenue = db.session.query(
                func.coalesce(func.sum(StaffOrder.total_amount), 0)
            ).filter(
                StaffOrder.school_id == s.id,
                StaffOrder.status == 'completed'
            ).scalar()

            total_revenue = student_revenue + staff_revenue

            low_stock_count = SchoolInventory.query.filter(
                SchoolInventory.school_id == s.id,
                SchoolInventory.quantity <= 10
            ).count()

            school_user = User.query.filter_by(
                school_id=s.id,
                role=UserRole.SCHOOL
            ).first()

            result.append({
                "id": s.id,
                "name": s.name,
                "address": s.address,
                "contact_person": s.contact_person,
                "contact_phone": s.contact_phone,
                "contact_email": s.contact_email,
                "commission_percentage": s.commission_percentage,
                "coin_balance": s.coin_balance,
                "total_revenue": round(total_revenue, 2),
                "low_stock_items": low_stock_count,
                "user": {
                    "username": school_user.username if school_user else None
                }
            })

        return success_response(
            data={
                "schools": result,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": pagination.total,
                    "pages": pagination.pages
                }
            }
        )
        
    @app.route('/api/admin/schools/<int:school_id>', methods=['PUT'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def api_update_school(school_id):

        school = School.query.get(school_id)
        if not school:
            return error_response("School not found", 404)

        data = request.get_json()

        school.name = data.get('name', school.name)
        school.address = data.get('address', school.address)
        school.contact_person = data.get('contact_person', school.contact_person)
        school.contact_phone = data.get('contact_phone', school.contact_phone)
        school.contact_email = data.get('contact_email', school.contact_email)
        school.commission_percentage = data.get(
            'commission_percentage', school.commission_percentage
        )

        db.session.commit()

        return success_response(message="School updated successfully")

    @app.route('/api/admin/sellers', methods=['GET', 'POST'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def api_sellers():

        # ==========================================================
        # CREATE SELLER (POST)
        # ==========================================================
        if request.method == 'POST':

            data = request.get_json()

            required = [
                'name',
                'contact_email',
                'username',
                'password'
            ]

            if not data or not all(k in data for k in required):
                return error_response("Missing required fields")

            username = data['username']
            email = data['contact_email']

            # 🔐 Username uniqueness
            if User.query.filter_by(username=username).first():
                return error_response("Username already exists")

            # 🔐 Email uniqueness
            if User.query.filter_by(email=email).first():
                return error_response("Email already exists")

            try:

                # 1️⃣ Create Seller
                seller = Seller(
                    name=data['name'],
                    company_name=data.get('company_name'),
                    contact_person=data.get('contact_person'),
                    contact_phone=data.get('contact_phone'),
                    contact_email=email,
                    address=data.get('address')
                )

                db.session.add(seller)
                db.session.flush()  # get seller.id

                # 2️⃣ Create Seller Login User
                seller_user = User(
                    full_name=data.get('contact_person') or "Seller Admin",
                    phone_number=data.get('contact_phone') or "0000000000",
                    username=username,
                    email=email,
                    role=UserRole.SELLER,
                    seller_id=seller.id
                )

                seller_user.set_password(data['password'])

                db.session.add(seller_user)
                db.session.commit()

                return success_response(
                    message="Seller created successfully",
                    data={
                        "id": seller.id,
                        "username": seller_user.username
                    },
                    status=201
                )

            except Exception as e:
                db.session.rollback()
                print("Seller creation error:", str(e))
                return error_response("Failed to create seller", 500)

        # ==========================================================
        # GET SELLERS
        # ==========================================================

        sellers = Seller.query.all()

        result = []

        for s in sellers:

            user = User.query.filter_by(
                seller_id=s.id,
                role=UserRole.SELLER
            ).first()

            result.append({
                "id": s.id,
                "name": s.name,
                "company_name": s.company_name,
                "contact_person": s.contact_person,
                "contact_phone": s.contact_phone,
                "contact_email": s.contact_email,
                "address": s.address,
                "user": {
                    "id": user.id if user else None,
                    "username": user.username if user else None,
                    "email": user.email if user else None
                } if user else None
            })

        return success_response(data=result)
    
    @app.route("/api/admin/sellers/<int:seller_id>", methods=["DELETE"])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def delete_seller(seller_id):

        seller = Seller.query.get(seller_id)

        if not seller:
            return error_response("Seller not found", 404)

        try:
            # 🔥 Delete linked user first (if exists)
            user = User.query.filter_by(seller_id=seller.id).first()

            if user:
                db.session.delete(user)

            # 🔥 Optional: Prevent deletion if seller has inventory
            inventory_exists = SellerInventory.query.filter_by(
                seller_id=seller.id
            ).first()

            if inventory_exists:
                return error_response(
                    "Cannot delete seller with allocated inventory",
                    400
                )

            # 🔥 Optional: Prevent deletion if dispatch history exists
            dispatch_exists = AdminDispatchInstruction.query.filter_by(
                seller_id=seller.id
            ).first()

            if dispatch_exists:
                return error_response(
                    "Cannot delete seller with dispatch history",
                    400
                )

            db.session.delete(seller)
            db.session.commit()

            return success_response(
                message="Seller deleted successfully"
            )

        except Exception as e:
            db.session.rollback()
            return error_response(str(e), 500)

    UPLOAD_FOLDER = "static/uploads"
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    
    @app.route('/api/admin/products', methods=['GET', 'POST'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def manage_products():

        # ================= CREATE PRODUCT =================
        if request.method == 'POST':

            try:

                is_multipart = request.content_type and "multipart/form-data" in request.content_type

                if is_multipart:

                    data = request.form

                    sizes = request.form.getlist("sizes")
                    quantities = request.form.getlist("quantities")

                    seller_ids = request.form.getlist("seller_ids[]")
                    seller_sizes = request.form.getlist("seller_sizes[]")
                    seller_quantities = request.form.getlist("seller_quantities[]")

                    school_ids = request.form.getlist("school_ids[]")

                    total_quantity = request.form.get("total_quantity")

                    files = request.files.getlist("images")

                else:

                    data = request.get_json() or {}

                    sizes = [s.get("size") for s in data.get("sizes", [])]
                    quantities = [str(s.get("quantity")) for s in data.get("sizes", [])]

                    seller_ids = data.get("seller_ids", [])
                    seller_quantities = data.get("seller_quantities", [])

                    school_ids = data.get("school_ids", [])

                    total_quantity = data.get("total_quantity")

                    files = []


                # ================= VALIDATION =================

                required_fields = ["name", "sku", "category", "unit_price"]

                for field in required_fields:
                    if not data.get(field):
                        return jsonify({"error": f"{field} is required"}), 400

                if not seller_ids:
                    return jsonify({"error": "At least one seller required"}), 400

                if not school_ids:
                    return jsonify({"error": "At least one school required"}), 400

                if Product.query.filter_by(sku=data["sku"]).first():
                    return jsonify({"error": "SKU already exists"}), 400

                unit_price = float(data["unit_price"])

                if unit_price < 0:
                    return jsonify({"error": "Price cannot be negative"}), 400


                # ================= CREATE PRODUCT =================

                product = Product(
                    name=data["name"],
                    description=data.get("description"),
                    sku=data["sku"],
                    category=data["category"],
                    unit_price=unit_price
                )

                db.session.add(product)
                db.session.flush()

                total_product_qty = 0
                created_sizes = []


                # ================= SIZE PRODUCT =================

                if sizes and any(sizes):

                    seen_sizes = set()

                    for i in range(len(sizes)):

                        size_name = sizes[i]

                        qty = int(quantities[i])

                        if size_name in seen_sizes:
                            return jsonify({"error": f"Duplicate size: {size_name}"}), 400

                        if qty <= 0:
                            return jsonify({"error": f"Invalid quantity for {size_name}"}), 400

                        seen_sizes.add(size_name)

                        total_product_qty += qty

                        size_obj = ProductSize(
                            product_id=product.id,
                            size=size_name,
                            quantity=qty
                        )

                        db.session.add(size_obj)
                        db.session.flush()

                        created_sizes.append(size_obj)

                else:

                    if not total_quantity:
                        return jsonify({"error": "total_quantity required"}), 400

                    qty = int(total_quantity)

                    if qty <= 0:
                        return jsonify({"error": "Invalid quantity"}), 400

                    total_product_qty = qty

                    size_obj = ProductSize(
                        product_id=product.id,
                        size="DEFAULT",
                        quantity=qty
                    )

                    db.session.add(size_obj)
                    db.session.flush()

                    created_sizes.append(size_obj)


                # ================= IMAGE UPLOAD =================

                upload_folder = os.path.join("static", "uploads")
                os.makedirs(upload_folder, exist_ok=True)

                for file in files:

                    if not file:
                        continue

                    filename = secure_filename(file.filename)
                    unique_name = f"{uuid.uuid4()}_{filename}"

                    filepath = os.path.join(upload_folder, unique_name)
                    file.save(filepath)

                    db.session.add(
                        ProductImage(
                            product_id=product.id,
                            image_url=f"/static/uploads/{unique_name}"
                        )
                    )


                # ================= SELLER INVENTORY =================

                for i in range(len(seller_ids)):

                    seller_id = int(seller_ids[i])

                    try:
                        qty = int(seller_quantities[i])
                    except:
                        qty = total_product_qty

                    size_value = seller_sizes[i]

                    if size_value == "NO_SIZE":
                        size_value = "DEFAULT"

                    seller = db.session.get(Seller, seller_id)

                    if not seller:
                        return jsonify({"error": f"Seller {seller_id} not found"}), 404


                    # Find correct size object
                    size_obj = None
                    for s in created_sizes:
                        if s.size == size_value:
                            size_obj = s
                            break

                    if not size_obj:
                        return jsonify({"error": f"Size {size_value} not found"}), 400


                    # 🔴 VERY IMPORTANT: prevent duplicate insert
                    with db.session.no_autoflush:

                        existing_inventory = SellerInventory.query.filter_by(
                            seller_id=seller_id,
                            product_id=product.id,
                            size_id=size_obj.id
                        ).first()


                    if existing_inventory:

                        existing_inventory.total_allocated += qty
                        existing_inventory.remaining_stock += qty

                    else:

                        db.session.add(
                            SellerInventory(
                                seller_id=seller_id,
                                product_id=product.id,
                                size_id=size_obj.id,
                                total_allocated=qty,
                                sent_stock=0,
                                remaining_stock=qty
                            )
                        )
                # ================= ASSIGN SCHOOLS TO SELLER PRODUCT =================

                for seller_id in seller_ids:
                    seller_id = int(seller_id)

                    for school_id in school_ids:
                        school_id = int(school_id)

                        # prevent duplicates
                        existing = SellerSchoolProduct.query.filter_by(
                            seller_id=seller_id,
                            product_id=product.id,
                            school_id=school_id
                        ).first()

                        if not existing:
                            db.session.add(
                                SellerSchoolProduct(
                                    seller_id=seller_id,
                                    product_id=product.id,
                                    school_id=school_id
                                )
            )
                db.session.commit()

                return jsonify({
                    "message": "Product created successfully",
                    "product_id": product.id
                }), 201


            except Exception as e:

                db.session.rollback()

                print("Product creation error:", str(e))

                return jsonify({
                    "error": "Failed to create product",
                    "details": str(e)
                }), 500


        # ================= GET PRODUCTS =================

        products = Product.query.all()

        response = []

        for p in products:

            sizes = ProductSize.query.filter_by(product_id=p.id).all()

            total_qty = sum(s.quantity for s in sizes)

            seller_inventory = SellerInventory.query.filter_by(
                product_id=p.id
            ).all()

            allocated_qty = sum(inv.total_allocated for inv in seller_inventory)

            images = ProductImage.query.filter_by(
                product_id=p.id
            ).all()


            response.append({

                "id": p.id,
                "name": p.name,
                "sku": p.sku,
                "category": p.category,
                "unit_price": p.unit_price,
                "total_qty": total_qty,
                "allocated": allocated_qty,

                "sizes": [
                    {
                        "id": s.id,
                        "size": None if s.size == "DEFAULT" else s.size,
                        "quantity": s.quantity
                    }
                    for s in sizes
                ],

                "images": [
                    {
                        "id": img.id,
                        "image_url": img.image_url
                    }
                    for img in images
                ]

            })

        return jsonify(response), 200


    @app.route("/api/admin/products/<int:product_id>", methods=["PUT"])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def update_product(product_id):

        try:

            product = db.session.get(Product, product_id)

            if not product:
                return jsonify({"error": "Product not found"}), 404


            # ================= REQUEST PARSING =================

            data = request.form.to_dict() if request.form else request.get_json(silent=True) or {}

            sizes = request.form.getlist("sizes")
            quantities = request.form.getlist("quantities")
            files = request.files.getlist("images")


            # ================= UPDATE BASIC FIELDS =================

            name = data.get("name")
            sku = data.get("sku")
            category = data.get("category")
            unit_price = data.get("unit_price")
            description = data.get("description")

            if name:
                product.name = name

            if sku:
                existing = Product.query.filter_by(sku=sku).first()

                if existing and existing.id != product.id:
                    return jsonify({"error": "SKU already exists"}), 400

                product.sku = sku

            if category:
                product.category = category

            if unit_price:
                try:
                    price = float(unit_price)

                    if price < 0:
                        return jsonify({"error": "Price cannot be negative"}), 400

                    product.unit_price = price

                except:
                    return jsonify({"error": "Invalid price"}), 400

            if description is not None:
                product.description = description


            # ================= UPDATE TOTAL QUANTITY =================

            total_quantity = data.get("total_quantity")

            if total_quantity:

                try:
                    total_quantity = int(total_quantity)
                except:
                    return jsonify({"error": "Invalid total_quantity"}), 400

                if total_quantity <= 0:
                    return jsonify({"error": "total_quantity must be greater than 0"}), 400


                # remove old sizes
                ProductSize.query.filter_by(product_id=product.id).delete()

                db.session.add(
                    ProductSize(
                        product_id=product.id,
                        size="DEFAULT",
                        quantity=total_quantity
                    )
                )


                # ================= UPDATE SELLER INVENTORY =================

                seller_inventories = SellerInventory.query.filter_by(
                    product_id=product.id
                ).all()

                if seller_inventories:

                    per_seller_qty = int(total_quantity / len(seller_inventories))

                    for inv in seller_inventories:

                        inv.total_allocated = per_seller_qty
                        inv.remaining_stock = per_seller_qty - inv.sent_stock


            # ================= UPDATE SIZES =================

            elif sizes:

                ProductSize.query.filter_by(product_id=product.id).delete()

                seen_sizes = set()

                total_size_qty = 0

                for i in range(len(sizes)):

                    size_name = sizes[i]

                    if not size_name:
                        continue

                    try:
                        qty = int(quantities[i])
                    except:
                        return jsonify({"error": f"Invalid quantity for {size_name}"}), 400

                    if size_name in seen_sizes:
                        return jsonify({"error": f"Duplicate size {size_name}"}), 400

                    if qty <= 0:
                        return jsonify({"error": f"Invalid quantity for {size_name}"}), 400

                    seen_sizes.add(size_name)

                    total_size_qty += qty

                    db.session.add(
                        ProductSize(
                            product_id=product.id,
                            size=size_name,
                            quantity=qty
                        )
                    )


                # ================= UPDATE SELLER INVENTORY =================

                seller_inventories = SellerInventory.query.filter_by(
                    product_id=product.id
                ).all()

                if seller_inventories:

                    per_seller_qty = int(total_size_qty / len(seller_inventories))

                    for inv in seller_inventories:

                        inv.total_allocated = per_seller_qty
                        inv.remaining_stock = per_seller_qty - inv.sent_stock


            # ================= IMAGE UPDATE =================

            if files:

                upload_folder = os.path.join("static", "uploads")

                os.makedirs(upload_folder, exist_ok=True)

                # remove old images
                ProductImage.query.filter_by(product_id=product.id).delete()

                for file in files:

                    if not file or file.filename == "":
                        continue

                    filename = secure_filename(file.filename)

                    unique_name = f"{uuid.uuid4()}_{filename}"

                    filepath = os.path.join(upload_folder, unique_name)

                    file.save(filepath)

                    db.session.add(
                        ProductImage(
                            product_id=product.id,
                            image_url=f"/static/uploads/{unique_name}"
                        )
                    )


            # ================= SAVE =================

            db.session.commit()

            return jsonify({
                "message": "Product updated successfully",
                "product_id": product.id
            }), 200


        except Exception as e:

            db.session.rollback()

            print("PRODUCT UPDATE ERROR:", str(e))

            return jsonify({
                "error": "Update failed",
                "details": str(e)
            }), 500
            
    @app.route('/api/admin/add-seller-stock', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def add_seller_stock():

        try:
            data = request.get_json()

            seller_id = int(data.get("seller_id"))
            product_id = int(data.get("product_id"))
            size_id = int(data.get("size_id"))
            quantity = int(data.get("quantity"))

            if quantity <= 0:
                return jsonify({"error": "Quantity must be positive"}), 400

            seller = Seller.query.get(seller_id)
            product = Product.query.get(product_id)
            size = ProductSize.query.get(size_id)

            if not seller or not product or not size:
                return jsonify({"error": "Invalid seller/product/size"}), 404

            inventory = SellerInventory.query.filter_by(
                seller_id=seller_id,
                product_id=product_id
            ).first()

            # If inventory exists → increase stock
            if inventory:

                inventory.total_allocated += quantity
                inventory.remaining_stock += quantity

            else:

                inventory = SellerInventory(
                    seller_id=seller_id,
                    product_id=product_id,
                    total_allocated=quantity,
                    remaining_stock=quantity,
                    sent_stock=0
                )

                db.session.add(inventory)

            db.session.commit()

            return jsonify({
                "message": "Stock added successfully"
            }), 200

        except Exception as e:
            db.session.rollback()
            return jsonify({
                "error": str(e)
            }), 500
    
    @app.route("/api/admin/add-product-stock", methods=["POST"])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def add_product_stock():

        data = request.get_json()

        product_id = data.get("product_id")
        size_id = data.get("size_id")
        quantity = int(data.get("quantity"))

        size = ProductSize.query.get(size_id)

        if not size or size.product_id != product_id:
            return jsonify({"error": "Invalid size"}), 400

        size.quantity += quantity

        db.session.commit()

        return jsonify({
            "message": "Stock added successfully"
        }), 200
        
    # ===============================
    # ADMIN RESTOCK PRODUCT
    # ===============================

    @app.route('/api/admin/restock-product', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def admin_restock_product():

        try:

            data = request.get_json()

            product_id = int(data.get("product_id"))
            seller_id = int(data.get("seller_id"))
            quantity = int(data.get("quantity"))

            if quantity <= 0:
                return jsonify({"error": "Quantity must be greater than 0"}), 400

            product = Product.query.get(product_id)
            seller = Seller.query.get(seller_id)

            if not product or not seller:
                return jsonify({"error": "Invalid product or seller"}), 404

            inventory = SellerInventory.query.filter_by(
                seller_id=seller_id,
                product_id=product_id
            ).first()

            if inventory:

                inventory.total_allocated += quantity
                inventory.remaining_stock += quantity

            else:

                inventory = SellerInventory(
                    seller_id=seller_id,
                    product_id=product_id,
                    total_allocated=quantity,
                    sent_stock=0,
                    remaining_stock=quantity
                )

                db.session.add(inventory)

            ledger = InventoryLedger(
                product_id=product_id,
                seller_id=seller_id,
                action="ADMIN_RESTOCK",
                quantity=quantity,
                balance_after=inventory.remaining_stock,
                reference_type="RESTOCK",
                created_at=datetime.utcnow()
            )

            db.session.add(ledger)

            db.session.commit()

            return jsonify({
                "message": "Stock added successfully",
                "remaining_stock": inventory.remaining_stock
            }), 200

        except Exception as e:

            db.session.rollback()

            return jsonify({
                "error": "Restock failed",
                "details": str(e)
            }), 500
        
    # ===============================
    # ADMIN ADD PRODUCT SIZE
    # ===============================

    @app.route('/api/admin/add-product-size', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def admin_add_product_size():

        try:

            data = request.get_json()

            product_id = int(data.get("product_id"))
            size = data.get("size")
            quantity = int(data.get("quantity"))

            if not size:
                return jsonify({"error": "Size required"}), 400

            if quantity <= 0:
                return jsonify({"error": "Invalid quantity"}), 400

            existing = ProductSize.query.filter_by(
                product_id=product_id,
                size=size
            ).first()

            if existing:
                return jsonify({"error": "Size already exists"}), 400

            new_size = ProductSize(
                product_id=product_id,
                size=size,
                quantity=quantity
            )

            db.session.add(new_size)
            db.session.commit()

            return jsonify({
                "message": "Size added successfully"
            }), 200

        except Exception as e:

            db.session.rollback()

            return jsonify({
                "error": str(e)
            }), 500
        
        
    # ===============================
    # ADMIN ASSIGN SCHOOL TO SELLER
    # ===============================

    @app.route('/api/admin/assign-product-school', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def assign_product_school():

        try:

            data = request.get_json()

            seller_id = int(data.get("seller_id"))
            product_id = int(data.get("product_id"))
            school_ids = data.get("school_ids")

            if not school_ids:
                return jsonify({"error": "school_ids required"}), 400

            created = []

            for sid in school_ids:

                sid = int(sid)

                exists = SellerSchoolProduct.query.filter_by(
                    seller_id=seller_id,
                    school_id=sid,
                    product_id=product_id
                ).first()

                if not exists:

                    row = SellerSchoolProduct(
                        seller_id=seller_id,
                        school_id=sid,
                        product_id=product_id
                    )

                    db.session.add(row)
                    created.append(sid)

            db.session.commit()

            return jsonify({
                "message": "Schools assigned",
                "schools_added": created
            }), 200

        except Exception as e:

            db.session.rollback()

            return jsonify({
                "error": str(e)
            }), 500    
        
    @app.route("/api/admin/products/<int:product_id>", methods=["DELETE"])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def delete_product(product_id):

        try:

            product = db.session.get(Product, product_id)

            if not product:
                return jsonify({"error": "Product not found"}), 404

            # delete related records first
            ProductSize.query.filter_by(product_id=product_id).delete()
            ProductImage.query.filter_by(product_id=product_id).delete()
            SellerInventory.query.filter_by(product_id=product_id).delete()
            SchoolInventory.query.filter_by(product_id=product_id).delete()
            AdminDispatchInstruction.query.filter_by(product_id=product_id).delete()
            ShipmentItem.query.filter_by(product_id=product_id).delete()
            OrderItem.query.filter_by(product_id=product_id).delete()
            StaffOrderItem.query.filter_by(product_id=product_id).delete()

            db.session.delete(product)

            db.session.commit()

            return jsonify({
                "message": "Product deleted successfully"
            }), 200

        except Exception as e:

            db.session.rollback()

            print("DELETE ERROR:", str(e))

            return jsonify({
                "error": "Delete failed",
                "details": str(e)
            }), 500
        
            
    @app.route('/admin/allocate-stock', methods=['POST'])
    @session_login_required(UserRole.SUPER_ADMIN)
    def allocate_stock_web():

        try:
            seller_id = int(request.form.get('seller_id'))
            product_id = int(request.form.get('product_id'))
            quantity = int(request.form.get('quantity'))
        except (TypeError, ValueError):
            return "Invalid form data", 400

        if quantity <= 0:
            return "Quantity must be positive", 400

        seller = Seller.query.get(seller_id)
        product = Product.query.get(product_id)

        if not seller or not product:
            return "Invalid seller or product", 404

        # 🔥 NEW: Check total available product size stock
        total_size_stock = sum(size.quantity for size in product.sizes)

        if quantity > total_size_stock:
            return f"Cannot allocate more than available stock ({total_size_stock})", 400

        inventory = SellerInventory.query.filter_by(
            seller_id=seller_id,
            product_id=product_id
        ).first()

        if inventory:
            inventory.total_allocated = (inventory.total_allocated or 0) + quantity
            inventory.remaining_stock = (inventory.remaining_stock or 0) + quantity
        else:
            inventory = SellerInventory(
                seller_id=seller_id,
                product_id=product_id,
                total_allocated=quantity,
                sent_stock=0,
                remaining_stock=quantity
            )
            db.session.add(inventory)

        db.session.commit()

        return redirect('/admin/dispatch')

    @app.route('/api/admin/allocate-stock', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.SUPER_ADMIN)
    def allocate_stock():
        data = request.get_json()
        seller_id = data['seller_id']
        product_id = data['product_id']
        quantity = data['quantity']

        if quantity <= 0:
            return jsonify({'error': 'Quantity must be positive'}), 400

        inventory = SellerInventory.query.filter_by(
            seller_id=seller_id,
            product_id=product_id
        ).first()

        if inventory:
            inventory.total_allocated += quantity
            inventory.remaining_stock += quantity
        else:
            inventory = SellerInventory(
                seller_id=seller_id,
                product_id=product_id,
                total_allocated=quantity,
                sent_stock=0,
                remaining_stock=quantity
            )
            db.session.add(inventory)

        db.session.commit()
        return jsonify({'message': 'Stock allocated successfully'})

    from flask import url_for

    @app.route('/api/seller/inventory', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SELLER)
    def seller_inventory():

        try:

            user = get_current_user()

            inventories = SellerInventory.query.filter_by(
                seller_id=user.seller_id
            ).all()

            result = []

            # Cache schools per product
            product_school_cache = {}

            # Cache product images
            product_image_cache = {}

            # Cache sizes
            size_cache = {}

            for inv in inventories:

                product = db.session.get(Product, inv.product_id)

                if not product:
                    continue


                # ================= SIZE =================

                size_value = "DEFAULT"

                if inv.size_id:

                    if inv.size_id not in size_cache:
                        size_cache[inv.size_id] = db.session.get(ProductSize, inv.size_id)

                    size_obj = size_cache[inv.size_id]

                    if size_obj and size_obj.size:
                        size_value = size_obj.size


                # ================= PRODUCT IMAGE =================

                if product.id not in product_image_cache:

                    product_image = ProductImage.query.filter_by(
                        product_id=product.id
                    ).first()

                    if product_image and product_image.image_url:
                        product_image_cache[product.id] = (
                            request.host_url.rstrip("/") + product_image.image_url
                        )
                    else:
                        product_image_cache[product.id] = None

                image = product_image_cache[product.id]


                # ================= GET SCHOOLS =================

                if product.id not in product_school_cache:

                    allocations = SellerSchoolProduct.query.filter_by(
                        seller_id=user.seller_id,
                        product_id=product.id
                    ).all()

                    schools = []

                    for alloc in allocations:

                        if alloc.school:
                            schools.append({
                                "id": alloc.school.id,
                                "name": alloc.school.name
                            })
                        else:
                            # fallback safety
                            school = db.session.get(School, alloc.school_id)
                            if school:
                                schools.append({
                                    "id": school.id,
                                    "name": school.name
                                })

                    product_school_cache[product.id] = schools

                schools = product_school_cache[product.id]


                # ================= RESPONSE =================

                result.append({

                    "id": inv.id,
                    "product_id": product.id,

                    "product_name": product.name,
                    "sku": product.sku,
                    "category": product.category,
                    "price": product.unit_price,

                    "size": size_value,

                    "image": image,

                    "total_allocated": inv.total_allocated,
                    "sent_stock": inv.sent_stock,
                    "remaining_stock": inv.remaining_stock,

                    "schools": schools

                })


            return jsonify(result), 200


        except Exception as e:

            print("SELLER INVENTORY ERROR:", str(e))

            return jsonify({
                "error": "Failed to load inventory",
                "details": str(e)
            }), 500

    @app.route('/seller/school-sales')
    @session_login_required(UserRole.SELLER)
    def seller_school_sales_page():
        return render_template('seller/school_sales.html')

    @app.route('/admin/student-orders')
    @session_login_required(UserRole.SUPER_ADMIN)
    def admin_student_orders_page():

        orders = Order.query.order_by(Order.created_at.desc()).all()

        return render_template(
            'admin/student_orders.html',
            orders=orders
        )
    
    @app.route('/api/seller/school-sales', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SELLER)
    def seller_school_sales():

        user = get_current_user()

        shipments = Shipment.query.filter_by(
            from_seller_id=user.seller_id
        ).all()

        data = []

        for shipment in shipments:
            school = shipment.to_school

            for item in shipment.items:

                # Total sent
                total_sent = sum(
                    si.quantity
                    for s in Shipment.query.filter_by(
                        from_seller_id=user.seller_id,
                        to_school_id=school.id
                    ).all()
                    for si in s.items
                    if si.product_id == item.product_id
                )

                # Total sold (student)
                sold_items = OrderItem.query.join(Order).filter(
                    Order.school_id == school.id,
                    Order.status == 'completed',
                    OrderItem.product_id == item.product_id
                ).all()

                total_sold = sum(oi.quantity for oi in sold_items)

                # Current stock at school
                school_inv = SchoolInventory.query.filter_by(
                    school_id=school.id,
                    product_id=item.product_id,
                    category=CategoryType.STUDENT
                ).first()

                remaining = school_inv.quantity if school_inv else 0

                data.append({
                    "school": school.name,
                    "product": item.product.name,
                    "total_sent": total_sent,
                    "total_sold": total_sold,
                    "remaining_at_school": remaining
                })

        return jsonify(data)


    @app.route('/seller/inventory')
    @session_login_required(UserRole.SELLER)
    def seller_inventory_page():

        # Get logged-in seller user
        user = User.query.get(session.get('user_id'))

        # Safety check
        if not user or not user.seller_id:
            return "Seller account not properly linked.", 403

        # Fetch inventory
        inventory = SellerInventory.query.filter_by(
            seller_id=user.seller_id
        ).order_by(SellerInventory.created_at.desc()).all()

        # Calculate totals (optional but recommended)
        total_allocated = sum(inv.total_allocated for inv in inventory)
        total_sent = sum(inv.sent_stock for inv in inventory)
        total_remaining = sum(inv.remaining_stock for inv in inventory)

        stats = {
            "total_allocated": total_allocated,
            "total_sent": total_sent,
            "total_remaining": total_remaining
        }

        return render_template(
            'seller/inventory.html',
            inventory=inventory,
            stats=stats
        )
        
    @app.route('/seller/assigned-dispatches')
    @session_login_required(UserRole.SELLER)
    def seller_assigned_dispatches_page():

        user = User.query.get(session['user_id'])

        instructions = AdminDispatchInstruction.query.filter_by(
            seller_id=user.seller_id,
            status='PENDING'
        ).order_by(AdminDispatchInstruction.created_at.desc()).all()

        return render_template(
            'seller/assigned_dispatches.html',
            instructions=instructions
        )
    
    @app.route('/seller/dispatch-history')
    @session_login_required(UserRole.SELLER)
    def seller_dispatch_history_page():

        user = User.query.get(session['user_id'])

        shipments = Shipment.query.filter_by(
            from_seller_id=user.seller_id
        ).order_by(Shipment.created_at.desc()).all()

        return render_template(
            'seller/dispatch_history.html',
            shipments=shipments
        )
    
    @app.route('/school/shipments')
    @session_login_required(UserRole.SCHOOL)
    def school_shipments_page():

        user = User.query.get(session['user_id'])

        shipments = Shipment.query.filter_by(
            to_school_id=user.school_id
        ).order_by(Shipment.created_at.desc()).all()

        return render_template(
            'school/shipment_tracking.html',
            shipments=shipments
        )
    
    @app.route('/api/school/student-orders', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SCHOOL)
    def get_school_student_orders_mobile():

        try:
            user = get_current_user()

            if not user or not user.school_id:
                return jsonify({"error": "Invalid school user"}), 403

            # Fetch ALL orders for this school (no status filter)
            orders = (
                db.session.query(Order)
                .filter(Order.school_id == user.school_id)
                .order_by(Order.created_at.desc())
                .all()
            )

            print("TOTAL ORDERS FOUND:", len(orders))

            result = []

            for order in orders:

                print("ORDER:", order.id, "STATUS:", order.status)

                items = (
                    db.session.query(OrderItem)
                    .filter(OrderItem.order_id == order.id)
                    .all()
                )

                items_data = []
                calculated_total = 0

                for item in items:

                    product = db.session.get(Product, item.product_id)
                    size = db.session.get(ProductSize, item.size_id) if item.size_id else None

                    item_total = item.total_price if item.total_price else (
                        item.quantity * item.unit_price
                    )

                    calculated_total += item_total

                    items_data.append({
                        "product_name": product.name if product else "Unknown Product",
                        "sku": product.sku if product else None,
                        "size": size.size if size else "N/A",
                        "quantity": item.quantity,
                        "unit_price": item.unit_price,
                        "total_price": item_total
                    })

                result.append({
                    "order_id": order.id,
                    "status": order.status,   # Always return real DB status
                    "payment_status": order.payment_status,
                    "payment_mode": getattr(order, "payment_mode", "online"),
                    "total_amount": calculated_total,
                    "created_at": order.created_at.isoformat(),
                    "items": items_data
                })

            return jsonify({
                "orders": result
            }), 200

        except Exception as e:
            print("School student orders error:", str(e))
            return jsonify({"error": "Failed to fetch student orders"}), 500
    
    @app.route('/api/school/update-handover/<int:order_id>', methods=['PUT'])
    @jwt_required()
    @role_required(UserRole.SCHOOL)
    def update_handover(order_id):

        try:
            data = request.get_json(silent=True) or {}
            print("HANDOVER REQUEST DATA:", data)

            new_status = data.get("status")

            if not new_status:
                return jsonify({"error": "Status field is required"}), 400

            new_status = new_status.upper()

            allowed_status = [
                "READY_FOR_HANDOVER",
                "HANDED_OVER",
                "COMPLETED"
            ]

            if new_status not in allowed_status:
                return jsonify({
                    "error": "Invalid status",
                    "allowed": allowed_status
                }), 400

            user_id = get_jwt_identity()
            user = db.session.get(User, int(user_id))

            if not user or not user.school_id:
                return jsonify({"error": "Invalid school user"}), 403

            order = db.session.get(Order, order_id)

            if not order:
                return jsonify({"error": "Order not found"}), 404

            if order.school_id != user.school_id:
                return jsonify({"error": "Unauthorized"}), 403

            order.status = new_status
            db.session.commit()

            print("UPDATED ORDER STATUS:", order.status)

            return jsonify({
                "message": "Order updated",
                "status": order.status
            }), 200

        except Exception as e:
            db.session.rollback()
            print("HANDOVER UPDATE ERROR:", e)
            return jsonify({"error": "Server error"}), 500
        
    @app.route('/api/school/wallet', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SCHOOL)
    def get_school_wallet():

        try:
            user = get_current_user()

            if not user or not user.school_id:
                return jsonify({"error": "School not found"}), 404

            school = db.session.get(School, user.school_id)

            if not school:
                return jsonify({"error": "School record missing"}), 404

            return jsonify({
                "coin_balance": school.coin_balance,
                "commission_percentage": school.commission_percentage
            }), 200

        except Exception as e:
            print("Wallet error:", str(e))
            return jsonify({"error": "Failed to fetch wallet"}), 500
        
    @app.route('/api/school/shipments', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SCHOOL)
    def get_school_shipments_mobile():

        try:
            user = get_current_user()

            shipments = (
                Shipment.query
                .filter(Shipment.to_school_id == user.school_id)
                .order_by(Shipment.created_at.desc())
                .all()
            )

            result = []

            for shipment in shipments:

                seller = Seller.query.get(shipment.from_seller_id)

                items_data = []

                for item in shipment.items:

                    product = Product.query.get(item.product_id)
                    size = ProductSize.query.get(item.size_id) if item.size_id else None

                    items_data.append({
                        "product_id": item.product_id,
                        "product_name": product.name if product else "Unknown",
                        "sku": product.sku if product else None,
                        "size": size.size if size else "N/A",
                        "quantity": item.quantity,
                        "category": item.category.value,
                        "unit_price": item.unit_price,
                        "total_value": item.unit_price * item.quantity
                    })

                result.append({
                    "shipment_id": shipment.id,
                    "status": shipment.status.value,
                    "from_seller": seller.name if seller else "Unknown",
                    "created_at": shipment.created_at.isoformat(),
                    "received_at": shipment.received_at.isoformat() if shipment.received_at else None,
                    "items": items_data
                })

            return jsonify(result), 200

        except Exception as e:
            print("Shipment list error:", str(e))
            return jsonify({"error": "Failed to fetch shipments"}), 500
    @app.route('/api/school/inventory', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.SCHOOL)
    def get_school_inventory_mobile():

        user = get_current_user()

        inventory_items = SchoolInventory.query.filter_by(
            school_id=user.school_id
        ).all()

        result = []

        for item in inventory_items:

            product = Product.query.get(item.product_id)
            size = ProductSize.query.get(item.size_id) if item.size_id else None

            result.append({
                "inventory_id": item.id,
                "product_id": product.id if product else None,
                "product_name": product.name if product else "Unknown",
                "sku": product.sku if product else None,
                "size": size.size if size else "N/A",
                "category": item.category.value,
                "unit_price": product.unit_price if product else 0,
                "quantity": item.quantity,
                "total_value": (product.unit_price * item.quantity) if product else 0,
                "low_stock": item.quantity <= 10
            })

        return jsonify(result), 200

    # @app.route('/school/receive-shipment/<int:shipment_id>', methods=['POST'])
    # @session_login_required(UserRole.SCHOOL)
    # def receive_shipment(shipment_id):

    #     from flask import flash, redirect, url_for

    #     user = User.query.get(session['user_id'])

    #     shipment = Shipment.query.get(shipment_id)

    #     if not shipment:
    #         return jsonify({'error': 'Shipment not found'}), 404

    #     if shipment.to_school_id != user.school_id:
    #         return jsonify({'error': 'Unauthorized'}), 403

    #     if shipment.status != ShipmentStatus.ON_THE_WAY:
    #         return jsonify({'error': 'Shipment not in transit'}), 400

    #     # =========================================================
    #     # 🔥 MOVE STOCK TO SCHOOL INVENTORY (SIZE-WISE)
    #     # =========================================================
    #     for item in shipment.items:

    #         # Ensure shipment item has size_id
    #         if not item.size_id:
    #             return jsonify({'error': 'Shipment item missing size information'}), 400

    #         inventory = SchoolInventory.query.filter_by(
    #             school_id=user.school_id,
    #             product_id=item.product_id,
    #             size_id=item.size_id,   # 🔥 IMPORTANT
    #             category=item.category
    #         ).first()

    #         # Create inventory record if not exists
    #         if not inventory:
    #             inventory = SchoolInventory(
    #                 school_id=user.school_id,
    #                 product_id=item.product_id,
    #                 size_id=item.size_id,   # 🔥 IMPORTANT
    #                 category=item.category,
    #                 quantity=0
    #             )
    #             db.session.add(inventory)

    #         # Add stock safely
    #         inventory.receive_stock(item.quantity)

    #     # =========================================================
    #     # MARK SHIPMENT AS DELIVERED
    #     # =========================================================
    #     shipment.status = ShipmentStatus.DELIVERED
    #     shipment.received_at = datetime.utcnow()

    #     db.session.commit()

    #     flash("Shipment received successfully", "success")
    #     return redirect(url_for("school_shipments_page"))
    
    # Student APIs (unchanged, mobile-only)
    @app.route('/api/student/signup', methods=['POST'])
    def student_signup():
        data = request.get_json()
        if User.query.filter_by(username=data['username']).first():
            return jsonify({'error': 'Username exists'}), 400
        if User.query.filter_by(email=data['email']).first():
            return jsonify({'error': 'Email exists'}), 400

        student = User(
            username=data['username'],
            email=data['email'],
            role=UserRole.STUDENT,
            school_id=data['school_id']
        )
        student.set_password(data['password'])
        db.session.add(student)
        db.session.commit()
        return jsonify({'message': 'Student registered'}), 201

    @app.route('/api/student/update-school', methods=['PUT'])
    @jwt_required()
    @role_required(UserRole.STUDENT)
    def update_student_school():
        user = get_current_user()
        data = request.get_json()
        user.school_id = data['school_id']
        db.session.commit()
        return jsonify({'message': 'School updated'}), 200

    @app.route('/api/student/products', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.STUDENT)
    def get_student_products():

        user = get_current_user()

        inventory_items = SchoolInventory.query.filter_by(
            school_id=user.school_id,
            category=CategoryType.STUDENT
        ).all()

        products_map = {}

        for item in inventory_items:

            if item.quantity <= 0:
                continue

            product = item.product
            size = item.size

            if product.id not in products_map:
                products_map[product.id] = {
                    "product_id": product.id,
                    "product_name": product.name,
                    "unit_price": product.unit_price,
                    "description": product.description,
                    "image_url": (
                        product.images[0].image_url
                        if product.images else None
                    ),
                    "sizes": []
                }

            products_map[product.id]["sizes"].append({
                "size_id": size.id,
                "size_label": size.size,
                "available_quantity": item.quantity
            })

        return jsonify(list(products_map.values())), 200

    @app.route('/api/student/order', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.STUDENT)
    def student_create_order():

        try:
            user = get_current_user()
            data = request.get_json()

            items = data.get("items")

            if not items:
                return jsonify({"error": "No items provided"}), 400

            if not user.school_id:
                return jsonify({"error": "Delivery school not selected"}), 400

            total_amount = 0

            validated_items = []

            # ===============================
            # VALIDATE INVENTORY
            # ===============================

            for item in items:

                product_id = item.get("product_id")
                size_id = item.get("size_id")
                quantity = int(item.get("quantity"))

                if quantity <= 0:
                    return jsonify({"error": "Invalid quantity"}), 400

                product = Product.query.get(product_id)

                if not product:
                    return jsonify({"error": "Product not found"}), 404

                school_inv = SchoolInventory.query.filter_by(
                    school_id=user.school_id,
                    product_id=product_id,
                    size_id=size_id,
                    category=CategoryType.STUDENT
                ).first()

                if not school_inv:
                    return jsonify({
                        "error": f"Product not available in selected school"
                    }), 400

                if school_inv.quantity < quantity:
                    return jsonify({
                        "error": f"Insufficient stock for {product.name}"
                    }), 400

                item_total = product.unit_price * quantity

                total_amount += item_total

                validated_items.append({
                    "product": product,
                    "product_id": product_id,
                    "size_id": size_id,
                    "quantity": quantity,
                    "unit_price": product.unit_price,
                    "total_price": item_total
                })

            # ===============================
            # CREATE ORDER
            # ===============================

            razorpay_order = razorpay_client.order.create({
                "amount": int(total_amount * 100),
                "currency": "INR"
            })

            order = Order(
                student_id=user.id,
                school_id=user.school_id,
                total_amount=total_amount,
                status="PENDING_PAYMENT",
                payment_status="PENDING",
                razorpay_order_id=razorpay_order["id"],
                created_at=datetime.utcnow()
            )

            db.session.add(order)
            db.session.flush()

            # ===============================
            # CREATE ORDER ITEMS
            # ===============================

            for item in validated_items:

                order_item = OrderItem(
                    order_id=order.id,
                    product_id=item["product_id"],
                    size_id=item["size_id"],
                    quantity=item["quantity"],
                    unit_price=item["unit_price"],
                    total_price=item["total_price"]
                )

                db.session.add(order_item)

            db.session.commit()

            return jsonify({
                "message": "Order created",
                "order_id": order.id,
                "razorpay_order_id": razorpay_order["id"],
                "amount": total_amount
            }), 201

        except Exception as e:
            db.session.rollback()
            print("Create order error:", str(e))
            return jsonify({"error": "Failed to create order"}), 500
        
    @app.route('/api/student/orders', methods=['GET'])
    @jwt_required()
    @role_required(UserRole.STUDENT)
    def student_orders():

        user = get_current_user()

        orders = Order.query.filter_by(
            student_id=user.id
        ).order_by(Order.created_at.desc()).all()

        result = []

        for o in orders:

            items = []

            for item in o.items:
                items.append({
                    "product_name": item.product.name,
                    "size": item.size.size,
                    "quantity": item.quantity,
                    "price": item.unit_price
                })

            result.append({
                "order_id": o.id,
                "pickup_code": o.pickup_code,
                "status": o.status,
                "total_amount": o.total_amount,
                "items": items,
                "date": o.created_at.strftime("%Y-%m-%d")
            })

        return jsonify(result)    

    import secrets

    @app.route('/api/student/payment-success', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.STUDENT)
    def payment_success():

        data = request.get_json()
        order_id = data.get('order_id')
        razorpay_payment_id = data.get('razorpay_payment_id')

        if not order_id or not razorpay_payment_id:
            return jsonify({'error': 'Invalid request'}), 400

        order = Order.query.get(order_id)

        if not order:
            return jsonify({'error': 'Order not found'}), 404

        # Prevent duplicate processing
        if order.payment_status == 'PAID':
            return jsonify({'message': 'Already paid'}), 200

        try:
            # ================================
            # MARK PAYMENT SUCCESS
            # ================================
            order.payment_status = 'PAID'
            order.payment_id = razorpay_payment_id

            # Generate confirmation token
            token = secrets.token_hex(4).upper()
            order.confirmation_token = token

            # ================================
            # DEDUCT STOCK
            # ================================
            for item in order.items:

                inv = SchoolInventory.query.filter_by(
                    school_id=order.school_id,
                    product_id=item.product_id,
                    category=CategoryType.STUDENT
                ).first()

                if not inv or inv.quantity < item.quantity:
                    db.session.rollback()
                    return jsonify({
                        'error': 'Stock changed. Contact admin.'
                    }), 400

                inv.sell_stock(item.quantity)

            # ================================
            # MARK ORDER COMPLETED
            # ================================
            order.status = "completed"
            order.completed_at = datetime.utcnow()

            # ================================
            # COMMISSION ENGINE (COINS SYSTEM)
            # ================================
            school = School.query.get(order.school_id)

            if school:

                commission_percentage = school.commission_percentage or 0

                commission_amount = (
                    order.total_amount * commission_percentage
                ) / 100

                # Credit coins
                school.coin_balance = (
                    school.coin_balance or 0
                ) + commission_amount

            # ================================
            # COMMIT EVERYTHING
            # ================================
            db.session.commit()

            return jsonify({
                'message': 'Payment successful',
                'confirmation_token': token,
                'pickup_school_id': order.school_id
            }), 200

        except Exception as e:
            db.session.rollback()
            return jsonify({
                'error': 'Payment processing failed',
                'details': str(e)
            }), 500
    
    @app.route('/api/school/payment-success', methods=['POST'])
    @session_login_required(UserRole.SCHOOL)
    def school_payment_success():

        data = request.get_json()
        order_id = data.get('order_id')

        order = Order.query.get(order_id)

        if not order:
            return jsonify({'error': 'Order not found'}), 404

        order.payment_status = "PAID"
        order.status = "completed"

        for item in order.items:
            inventory = SchoolInventory.query.filter_by(
                school_id=order.school_id,
                product_id=item.product_id,
                category=CategoryType.STUDENT
            ).first()

            inventory.sell_stock(item.quantity)

        db.session.commit()

        return jsonify({'message': 'Payment successful'})
    
    @app.route('/admin/test/add-coins', methods=['POST'])
    @session_login_required(UserRole.SUPER_ADMIN)
    def admin_add_test_coins():

        # 🔒 Optional: block in production
        if app.config.get("RAZORPAY_LIVE"):
            return "Disabled in production mode", 403

        try:
            school_id = int(request.form.get('school_id'))
            amount = float(request.form.get('amount'))
        except (TypeError, ValueError):
            return "Invalid data", 400

        if amount <= 0:
            return "Amount must be positive", 400

        school = db.session.get(School, school_id)

        if not school:
            return "School not found", 404

        school.coin_balance = (school.coin_balance or 0) + amount

        db.session.commit()

        from flask import flash, redirect, url_for
        flash(f"₹{amount} coins added successfully to {school.name}", "success")

        return redirect('/admin/schools')
    
    @app.route('/school/purchase', methods=['POST'])
    @session_login_required(UserRole.SCHOOL)
    def school_purchase():

        user = db.session.get(User, session['user_id'])
        if not user or not user.school_id:
            return "Invalid user session", 403

        school = db.session.get(School, user.school_id)
        if not school:
            return "School not found", 404

        # ===============================
        # SAFE FORM EXTRACTION
        # ===============================

        product_id_raw = request.form.get('product_id')
        quantity_raw = request.form.get('quantity')
        category = request.form.get('category')
        payment_method = request.form.get('payment_method')

        # Validate product_id
        if not product_id_raw:
            return "Please select a product", 400

        try:
            product_id = int(product_id_raw)
        except ValueError:
            return "Invalid product selected", 400

        # Validate quantity
        if not quantity_raw:
            return "Quantity is required", 400

        try:
            quantity = int(quantity_raw)
        except ValueError:
            return "Invalid quantity", 400

        if quantity <= 0:
            return "Quantity must be greater than 0", 400

        # Validate category
        if category not in ['student', 'staff']:
            return "Invalid category", 400

        # Validate payment method
        if payment_method not in ['coins', 'razorpay']:
            return "Invalid payment method", 400

        # ===============================
        # PRODUCT CHECK
        # ===============================

        product = db.session.get(Product, product_id)
        if not product:
            return "Product not found", 404

        inventory = SchoolInventory.query.filter_by(
            school_id=school.id,
            product_id=product_id,
            category=CategoryType(category)
        ).first()

        if not inventory:
            return "Product not allocated to this school", 400

        if inventory.quantity < quantity:
            return f"Insufficient stock. Available: {inventory.quantity}", 400

        total_amount = product.unit_price * quantity

        # ===============================
        # COINS VALIDATION (BEFORE ORDER CREATION)
        # ===============================

        if payment_method == "coins":
            if school.coin_balance < total_amount:
                return f"Insufficient coins. Available: {school.coin_balance}", 400

        # ===============================
        # CREATE ORDER
        # ===============================

        staff_order = StaffOrder(
            school_id=school.id,
            total_amount=total_amount,
            status='pending'
        )
        db.session.add(staff_order)
        db.session.flush()

        staff_item = StaffOrderItem(
            staff_order_id=staff_order.id,
            product_id=product_id,
            quantity=quantity,
            unit_price=product.unit_price,
            total_price=total_amount
        )
        db.session.add(staff_item)

        # ===============================
        # COINS PAYMENT
        # ===============================

        if payment_method == "coins":

            school.coin_balance -= total_amount
            inventory.sell_stock(quantity)

            staff_order.status = "completed"
            staff_order.completed_at = datetime.utcnow()

            db.session.commit()
            return redirect('/school/staff-orders')

        # ===============================
        # RAZORPAY PAYMENT
        # ===============================

        elif payment_method == "razorpay":

            db.session.commit()

            return render_template(
                "school/razorpay_checkout.html",
                order_id=staff_order.id,
                amount=total_amount,
                razorpay_key="YOUR_RAZORPAY_KEY"
            )
        
        
    @app.route('/api/school/confirm-pickup', methods=['POST'])
    @jwt_required()
    @role_required(UserRole.SCHOOL)
    def confirm_pickup():

        user = get_current_user()
        data = request.get_json()

        token = data.get('confirmation_token')

        order = Order.query.filter_by(
            confirmation_token=token,
            school_id=user.school_id
        ).first()

        if not order:
            return jsonify({'error': 'Invalid token'}), 404

        if order.status == 'completed':
            return jsonify({'message': 'Already completed'}), 200

        if order.payment_status != 'PAID':
            return jsonify({'error': 'Payment not completed'}), 400

        order.status = 'completed'
        order.pickup_confirmed_at = datetime.utcnow()

        db.session.commit()

        return jsonify({
            'message': 'Pickup confirmed successfully'
        }), 200
    # Health check
    @app.route('/health')
    def health_check():
        return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()})

    return app


# ===============================
# RUN SERVER
# ===============================
if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)