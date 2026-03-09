# auth.py
from flask import jsonify, request
from flask_jwt_extended import create_access_token
from models import db, User, UserRole

def login():
    """JWT login endpoint for app.py"""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    user = User.query.filter_by(username=username).first()
    
    if not user or not user.check_password(password):
        return jsonify({'error': 'Invalid credentials'}), 401
    
    access_token = create_access_token(identity=str(user.id))
    return jsonify({
        'access_token': access_token,
        'role': user.role.value,
        'user_id': user.id,
        'school_id': user.school_id,
        'seller_id': user.seller_id
    }), 200

def register_user():
    """Student registration endpoint"""
    data = request.get_json()
    
    # Check uniqueness
    if User.query.filter_by(username=data['username']).first():
        return jsonify({'error': 'Username already exists'}), 400
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'Email already exists'}), 400
    
    # Create student user
    student = User(
        username=data['username'],
        email=data['email'],
        role=UserRole.STUDENT,
        school_id=data['school_id']
    )
    student.set_password(data['password'])
    
    db.session.add(student)
    db.session.commit()
    
    return jsonify({'message': 'Student registered successfully'}), 201

def role_required(required_role):
    """Decorator to restrict access by role"""
    from functools import wraps
    from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
    
    def wrapper(fn):
        @wraps(fn)
        def decorator(*args, **kwargs):
            verify_jwt_in_request()
            user_id = get_jwt_identity()
            user = User.query.get(int(user_id))
            
            if not user or user.role != required_role:
                return jsonify({'error': 'Unauthorized'}), 403
            
            return fn(*args, **kwargs)
        return decorator
    return wrapper