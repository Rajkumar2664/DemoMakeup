import os
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from decimal import Decimal

load_dotenv()

app = Flask(__name__)

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'postgresql://makeup_user:makeup_password@db:5432/makeup_booking')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'dev-jwt-key')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=24)

db = SQLAlchemy(app)
migrate = Migrate(app, db)
CORS(app)
jwt = JWTManager(app)

# ============ MODELS ============
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    is_artist = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Artist(db.Model):
    __tablename__ = 'artists'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    specialty = db.Column(db.String(100), nullable=False)
    experience_years = db.Column(db.Integer, nullable=False)
    bio = db.Column(db.Text)
    rating = db.Column(db.Float, default=0.0)
    total_reviews = db.Column(db.Integer, default=0)
    price_per_hour = db.Column(db.Numeric(10, 2), nullable=False)
    is_available = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='artist_profile')

class Booking(db.Model):
    __tablename__ = 'bookings'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    artist_id = db.Column(db.Integer, db.ForeignKey('artists.id'), nullable=False)
    service_type = db.Column(db.String(100), nullable=False)
    booking_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    total_price = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(20), default='pending')
    notes = db.Column(db.Text)
    payment_status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    customer = db.relationship('User', foreign_keys=[customer_id])
    artist = db.relationship('Artist', foreign_keys=[artist_id])

# ============ ROUTES ============
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/app')
def app_ui():
    return render_template('index.html')

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    
    required_fields = ['email', 'password', 'first_name', 'last_name', 'phone']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing field: {field}'}), 400
    
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'User already exists'}), 409
    
    user = User(
        email=data['email'],
        first_name=data['first_name'],
        last_name=data['last_name'],
        phone=data['phone'],
        is_artist=data.get('is_artist', False)
    )
    user.set_password(data['password'])
    
    db.session.add(user)
    db.session.commit()
    
    if data.get('is_artist', False):
        artist = Artist(
            user_id=user.id,
            specialty=data.get('specialty', 'General Makeup'),
            experience_years=data.get('experience_years', 0),
            bio=data.get('bio', ''),
            price_per_hour=data.get('price_per_hour', 50.00)
        )
        db.session.add(artist)
        db.session.commit()
    
    return jsonify({
        'message': 'User registered successfully',
        'user_id': user.id
    }), 201

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    
    user = User.query.filter_by(email=data.get('email')).first()
    if not user or not user.check_password(data.get('password')):
        return jsonify({'error': 'Invalid credentials'}), 401
    
    access_token = create_access_token(identity=user.id)
    return jsonify({
        'access_token': access_token,
        'user': {
            'id': user.id,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'is_artist': user.is_artist
        }
    }), 200

@app.route('/api/artists', methods=['GET'])
def get_artists():
    artists = Artist.query.filter_by(is_available=True).all()
    return jsonify([{
        'id': artist.id,
        'name': f"{artist.user.first_name} {artist.user.last_name}",
        'specialty': artist.specialty,
        'experience_years': artist.experience_years,
        'rating': float(artist.rating) if artist.rating else 0,
        'total_reviews': artist.total_reviews,
        'price_per_hour': str(artist.price_per_hour),
        'bio': artist.bio
    } for artist in artists]), 200

@app.route('/api/bookings', methods=['GET'])
@jwt_required()
def get_bookings():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    
    if user.is_artist:
        artist = Artist.query.filter_by(user_id=user.id).first()
        if artist:
            bookings = Booking.query.filter_by(artist_id=artist.id).all()
        else:
            bookings = []
    else:
        bookings = Booking.query.filter_by(customer_id=user_id).all()
    
    return jsonify([{
        'id': booking.id,
        'service_type': booking.service_type,
        'booking_date': booking.booking_date.isoformat(),
        'start_time': booking.start_time.strftime('%H:%M'),
        'end_time': booking.end_time.strftime('%H:%M'),
        'total_price': str(booking.total_price),
        'status': booking.status,
        'payment_status': booking.payment_status
    } for booking in bookings]), 200

@app.route('/api/bookings', methods=['POST'])
@jwt_required()
def create_booking():
    user_id = get_jwt_identity()
    data = request.get_json()
    
    required_fields = ['artist_id', 'service_type', 'booking_date', 'start_time', 'end_time']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing field: {field}'}), 400
    
    artist = Artist.query.get(data['artist_id'])
    if not artist or not artist.is_available:
        return jsonify({'error': 'Artist not available'}), 404
    
    try:
        booking_date = datetime.strptime(data['booking_date'], '%Y-%m-%d').date()
        start_time = datetime.strptime(data['start_time'], '%H:%M').time()
        end_time = datetime.strptime(data['end_time'], '%H:%M').time()
    except ValueError:
        return jsonify({'error': 'Invalid date/time format'}), 400
    
    existing = Booking.query.filter_by(
        artist_id=artist.id,
        booking_date=booking_date,
        status='confirmed'
    ).filter(
        db.or_(
            db.and_(Booking.start_time <= start_time, Booking.end_time > start_time),
            db.and_(Booking.start_time < end_time, Booking.end_time >= end_time)
        )
    ).first()
    
    if existing:
        return jsonify({'error': 'Time slot already booked'}), 409
    
    start_datetime = datetime.combine(booking_date, start_time)
    end_datetime = datetime.combine(booking_date, end_time)
    hours = (end_datetime - start_datetime).total_seconds() / 3600
    total_price = Decimal(str(hours)) * artist.price_per_hour
    
    booking = Booking(
        customer_id=user_id,
        artist_id=artist.id,
        service_type=data['service_type'],
        booking_date=booking_date,
        start_time=start_time,
        end_time=end_time,
        total_price=total_price,
        notes=data.get('notes', ''),
        status='pending'
    )
    
    db.session.add(booking)
    db.session.commit()
    
    return jsonify({
        'message': 'Booking created successfully',
        'booking_id': booking.id,
        'total_price': str(total_price)
    }), 201

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Resource not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)    artist_id = db.Column(db.Integer, db.ForeignKey('artists.id'), nullable=False)
    service_type = db.Column(db.String(100), nullable=False)
    booking_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    total_price = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(20), default='pending')
    notes = db.Column(db.Text)
    payment_status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    customer = db.relationship('User', foreign_keys=[customer_id])
    artist = db.relationship('Artist', foreign_keys=[artist_id])

# ============ ROUTES ============
# Serve the UI
@app.route('/')
def index():
    return render_template('index.html')

# Serve the app UI at /app as well
@app.route('/app')
def app_ui():
    return render_template('index.html')

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    
    required_fields = ['email', 'password', 'first_name', 'last_name', 'phone']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing field: {field}'}), 400
    
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'User already exists'}), 409
    
    user = User(
        email=data['email'],
        first_name=data['first_name'],
        last_name=data['last_name'],
        phone=data['phone'],
        is_artist=data.get('is_artist', False)
    )
    user.set_password(data['password'])
    
    db.session.add(user)
    db.session.commit()
    
    if data.get('is_artist', False):
        artist = Artist(
            user_id=user.id,
            specialty=data.get('specialty', 'General Makeup'),
            experience_years=data.get('experience_years', 0),
            bio=data.get('bio', ''),
            price_per_hour=data.get('price_per_hour', 50.00)
        )
        db.session.add(artist)
        db.session.commit()
    
    return jsonify({
        'message': 'User registered successfully',
        'user_id': user.id
    }), 201

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    
    user = User.query.filter_by(email=data.get('email')).first()
    if not user or not user.check_password(data.get('password')):
        return jsonify({'error': 'Invalid credentials'}), 401
    
    access_token = create_access_token(identity=user.id)
    return jsonify({
        'access_token': access_token,
        'user': {
            'id': user.id,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'is_artist': user.is_artist
        }
    }), 200

@app.route('/api/artists', methods=['GET'])
def get_artists():
    artists = Artist.query.filter_by(is_available=True).all()
    return jsonify([{
        'id': artist.id,
        'name': f"{artist.user.first_name} {artist.user.last_name}",
        'specialty': artist.specialty,
        'experience_years': artist.experience_years,
        'rating': float(artist.rating) if artist.rating else 0,
        'total_reviews': artist.total_reviews,
        'price_per_hour': str(artist.price_per_hour),
        'bio': artist.bio
    } for artist in artists]), 200

@app.route('/api/bookings', methods=['GET'])
@jwt_required()
def get_bookings():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    
    if user.is_artist:
        artist = Artist.query.filter_by(user_id=user.id).first()
        if artist:
            bookings = Booking.query.filter_by(artist_id=artist.id).all()
        else:
            bookings = []
    else:
        bookings = Booking.query.filter_by(customer_id=user_id).all()
    
    return jsonify([{
        'id': booking.id,
        'service_type': booking.service_type,
        'booking_date': booking.booking_date.isoformat(),
        'start_time': booking.start_time.strftime('%H:%M'),
        'end_time': booking.end_time.strftime('%H:%M'),
        'total_price': str(booking.total_price),
        'status': booking.status,
        'payment_status': booking.payment_status
    } for booking in bookings]), 200

@app.route('/api/bookings', methods=['POST'])
@jwt_required()
def create_booking():
    user_id = get_jwt_identity()
    data = request.get_json()
    
    required_fields = ['artist_id', 'service_type', 'booking_date', 'start_time', 'end_time']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing field: {field}'}), 400
    
    artist = Artist.query.get(data['artist_id'])
    if not artist or not artist.is_available:
        return jsonify({'error': 'Artist not available'}), 404
    
    try:
        booking_date = datetime.strptime(data['booking_date'], '%Y-%m-%d').date()
        start_time = datetime.strptime(data['start_time'], '%H:%M').time()
        end_time = datetime.strptime(data['end_time'], '%H:%M').time()
    except ValueError:
        return jsonify({'error': 'Invalid date/time format'}), 400
    
    existing = Booking.query.filter_by(
        artist_id=artist.id,
        booking_date=booking_date,
        status='confirmed'
    ).filter(
        db.or_(
            db.and_(Booking.start_time <= start_time, Booking.end_time > start_time),
            db.and_(Booking.start_time < end_time, Booking.end_time >= end_time)
        )
    ).first()
    
    if existing:
        return jsonify({'error': 'Time slot already booked'}), 409
    
    start_datetime = datetime.combine(booking_date, start_time)
    end_datetime = datetime.combine(booking_date, end_time)
    hours = (end_datetime - start_datetime).total_seconds() / 3600
    total_price = Decimal(str(hours)) * artist.price_per_hour
    
    booking = Booking(
        customer_id=user_id,
        artist_id=artist.id,
        service_type=data['service_type'],
        booking_date=booking_date,
        start_time=start_time,
        end_time=end_time,
        total_price=total_price,
        notes=data.get('notes', ''),
        status='pending'
    )
    
    db.session.add(booking)
    db.session.commit()
    
    return jsonify({
        'message': 'Booking created successfully',
        'booking_id': booking.id,
        'total_price': str(total_price)
    }), 201

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Resource not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)        'status': 'running',
        'endpoints': {
            'health': '/api/health',
            'register': '/api/register',
            'login': '/api/login',
            'artists': '/api/artists',
            'bookings': '/api/bookings'
        }
    })

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    
    required_fields = ['email', 'password', 'first_name', 'last_name', 'phone']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing field: {field}'}), 400
    
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'User already exists'}), 409
    
    user = User(
        email=data['email'],
        first_name=data['first_name'],
        last_name=data['last_name'],
        phone=data['phone'],
        is_artist=data.get('is_artist', False)
    )
    user.set_password(data['password'])
    
    db.session.add(user)
    db.session.commit()
    
    if data.get('is_artist', False):
        artist = Artist(
            user_id=user.id,
            specialty=data.get('specialty', 'General Makeup'),
            experience_years=data.get('experience_years', 0),
            bio=data.get('bio', ''),
            price_per_hour=data.get('price_per_hour', 50.00)
        )
        db.session.add(artist)
        db.session.commit()
    
    return jsonify({
        'message': 'User registered successfully',
        'user_id': user.id
    }), 201

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    
    user = User.query.filter_by(email=data.get('email')).first()
    if not user or not user.check_password(data.get('password')):
        return jsonify({'error': 'Invalid credentials'}), 401
    
    access_token = create_access_token(identity=user.id)
    return jsonify({
        'access_token': access_token,
        'user': {
            'id': user.id,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'is_artist': user.is_artist
        }
    }), 200

@app.route('/api/artists', methods=['GET'])
def get_artists():
    artists = Artist.query.filter_by(is_available=True).all()
    return jsonify([{
        'id': artist.id,
        'name': f"{artist.user.first_name} {artist.user.last_name}",
        'specialty': artist.specialty,
        'experience_years': artist.experience_years,
        'rating': float(artist.rating) if artist.rating else 0,
        'total_reviews': artist.total_reviews,
        'price_per_hour': str(artist.price_per_hour),
        'bio': artist.bio
    } for artist in artists]), 200

@app.route('/api/bookings', methods=['GET'])
@jwt_required()
def get_bookings():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    
    if user.is_artist:
        artist = Artist.query.filter_by(user_id=user.id).first()
        if artist:
            bookings = Booking.query.filter_by(artist_id=artist.id).all()
        else:
            bookings = []
    else:
        bookings = Booking.query.filter_by(customer_id=user_id).all()
    
    return jsonify([{
        'id': booking.id,
        'service_type': booking.service_type,
        'booking_date': booking.booking_date.isoformat(),
        'start_time': booking.start_time.strftime('%H:%M'),
        'end_time': booking.end_time.strftime('%H:%M'),
        'total_price': str(booking.total_price),
        'status': booking.status,
        'payment_status': booking.payment_status
    } for booking in bookings]), 200

@app.route('/api/bookings', methods=['POST'])
@jwt_required()
def create_booking():
    user_id = get_jwt_identity()
    data = request.get_json()
    
    required_fields = ['artist_id', 'service_type', 'booking_date', 'start_time', 'end_time']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing field: {field}'}), 400
    
    artist = Artist.query.get(data['artist_id'])
    if not artist or not artist.is_available:
        return jsonify({'error': 'Artist not available'}), 404
    
    try:
        booking_date = datetime.strptime(data['booking_date'], '%Y-%m-%d').date()
        start_time = datetime.strptime(data['start_time'], '%H:%M').time()
        end_time = datetime.strptime(data['end_time'], '%H:%M').time()
    except ValueError:
        return jsonify({'error': 'Invalid date/time format'}), 400
    
    existing = Booking.query.filter_by(
        artist_id=artist.id,
        booking_date=booking_date,
        status='confirmed'
    ).filter(
        db.or_(
            db.and_(Booking.start_time <= start_time, Booking.end_time > start_time),
            db.and_(Booking.start_time < end_time, Booking.end_time >= end_time)
        )
    ).first()
    
    if existing:
        return jsonify({'error': 'Time slot already booked'}), 409
    
    start_datetime = datetime.combine(booking_date, start_time)
    end_datetime = datetime.combine(booking_date, end_time)
    hours = (end_datetime - start_datetime).total_seconds() / 3600
    total_price = Decimal(str(hours)) * artist.price_per_hour
    
    booking = Booking(
        customer_id=user_id,
        artist_id=artist.id,
        service_type=data['service_type'],
        booking_date=booking_date,
        start_time=start_time,
        end_time=end_time,
        total_price=total_price,
        notes=data.get('notes', ''),
        status='pending'
    )
    
    db.session.add(booking)
    db.session.commit()
    
    return jsonify({
        'message': 'Booking created successfully',
        'booking_id': booking.id,
        'total_price': str(total_price)
    }), 201

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Resource not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)
