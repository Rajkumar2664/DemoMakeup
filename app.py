import os
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import redis
from celery import Celery
from flask_mail import Mail, Message
import stripe
from decimal import Decimal

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# Configuration
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'postgresql://user:password@localhost:5432/makeup_booking')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'jwt-secret-key-change-in-production')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=24)

# Redis configuration for rate limiting
app.config['REDIS_URL'] = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# Email configuration
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')

# Stripe configuration
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

# Initialize extensions
db = SQLAlchemy(app)
migrate = Migrate(app, db)
CORS(app)
jwt = JWTManager(app)
mail = Mail(app)

# Initialize Redis
redis_client = redis.from_url(app.config['REDIS_URL'])

# Celery configuration
def make_celery(app):
    celery = Celery(
        app.import_name,
        backend=app.config['REDIS_URL'],
        broker=app.config['REDIS_URL']
    )
    celery.conf.update(app.config)
    
    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)
    
    celery.Task = ContextTask
    return celery

celery = make_celery(app)

# Models
class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    is_artist = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    bookings = db.relationship('Booking', backref='customer', lazy=True)
    
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
    bookings = db.relationship('Booking', backref='artist', lazy=True)
    availability = db.relationship('Availability', backref='artist', lazy=True)

class Availability(db.Model):
    __tablename__ = 'availability'
    
    id = db.Column(db.Integer, primary_key=True)
    artist_id = db.Column(db.Integer, db.ForeignKey('artists.id'), nullable=False)
    day_of_week = db.Column(db.Integer, nullable=False)  # 0-6 (Monday-Sunday)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    status = db.Column(db.String(20), default='pending')  # pending, confirmed, completed, cancelled
    notes = db.Column(db.Text)
    payment_status = db.Column(db.String(20), default='pending')
    stripe_payment_id = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Review(db.Model):
    __tablename__ = 'reviews'
    
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    artist_id = db.Column(db.Integer, db.ForeignKey('artists.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Celery tasks
@celery.task
def send_booking_confirmation_email(booking_id):
    booking = Booking.query.get(booking_id)
    if not booking:
        return
    
    customer = User.query.get(booking.customer_id)
    artist = Artist.query.get(booking.artist_id)
    
    msg = Message(
        subject='Booking Confirmation - Makeup Booking App',
        recipients=[customer.email],
        body=f"""
        Dear {customer.first_name},
        
        Your booking has been confirmed!
        
        Booking Details:
        - Service: {booking.service_type}
        - Date: {booking.booking_date}
        - Time: {booking.start_time} - {booking.end_time}
        - Artist: {artist.user.first_name} {artist.user.last_name}
        - Total Price: ${booking.total_price}
        
        Thank you for choosing our service!
        """
    )
    mail.send(msg)

@celery.task
def send_reminder_email(booking_id):
    booking = Booking.query.get(booking_id)
    if not booking or booking.status != 'confirmed':
        return
    
    customer = User.query.get(booking.customer_id)
    artist = Artist.query.get(booking.artist_id)
    
    msg = Message(
        subject='Reminder: Your Makeup Booking Tomorrow',
        recipients=[customer.email],
        body=f"""
        Dear {customer.first_name},
        
        This is a reminder for your makeup booking tomorrow:
        
        - Service: {booking.service_type}
        - Date: {booking.booking_date}
        - Time: {booking.start_time} - {booking.end_time}
        - Artist: {artist.user.first_name} {artist.user.last_name}
        
        We look forward to seeing you!
        """
    )
    mail.send(msg)

# Routes
@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()}), 200

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    
    # Validate input
    required_fields = ['email', 'password', 'first_name', 'last_name', 'phone']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing field: {field}'}), 400
    
    # Check if user exists
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'User already exists'}), 409
    
    # Create user
    user = User(
        email=data['email'],
        first_name=data['first_name'],
        last_name=data['last_name'],
        phone=data['phone']
    )
    user.set_password(data['password'])
    
    db.session.add(user)
    db.session.commit()
    
    # Create artist profile if specified
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
    
    return jsonify({'message': 'User registered successfully', 'user_id': user.id}), 201

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
    specialty = request.args.get('specialty')
    min_rating = request.args.get('min_rating', type=float)
    
    query = Artist.query.filter_by(is_available=True)
    
    if specialty:
        query = query.filter(Artist.specialty.ilike(f'%{specialty}%'))
    if min_rating:
        query = query.filter(Artist.rating >= min_rating)
    
    artists = query.all()
    
    return jsonify([{
        'id': artist.id,
        'name': f"{artist.user.first_name} {artist.user.last_name}",
        'specialty': artist.specialty,
        'experience_years': artist.experience_years,
        'rating': artist.rating,
        'total_reviews': artist.total_reviews,
        'price_per_hour': str(artist.price_per_hour),
        'bio': artist.bio
    } for artist in artists]), 200

@app.route('/api/artists/<int:artist_id>/availability', methods=['GET'])
def get_artist_availability(artist_id):
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'error': 'Date parameter required'}), 400
    
    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
    
    day_of_week = date.weekday()
    availability = Availability.query.filter_by(
        artist_id=artist_id,
        day_of_week=day_of_week
    ).all()
    
    # Get existing bookings for this date
    existing_bookings = Booking.query.filter_by(
        artist_id=artist_id,
        booking_date=date,
        status='confirmed'
    ).all()
    
    booked_slots = [{
        'start': booking.start_time.strftime('%H:%M'),
        'end': booking.end_time.strftime('%H:%M')
    } for booking in existing_bookings]
    
    return jsonify({
        'date': date_str,
        'available_slots': [{
            'start': avail.start_time.strftime('%H:%M'),
            'end': avail.end_time.strftime('%H:%M')
        } for avail in availability],
        'booked_slots': booked_slots
    }), 200

@app.route('/api/bookings', methods=['POST'])
@jwt_required()
def create_booking():
    user_id = get_jwt_identity()
    data = request.get_json()
    
    required_fields = ['artist_id', 'service_type', 'booking_date', 'start_time', 'end_time']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing field: {field}'}), 400
    
    # Validate artist exists and is available
    artist = Artist.query.get(data['artist_id'])
    if not artist or not artist.is_available:
        return jsonify({'error': 'Artist not available'}), 404
    
    # Parse date and time
    try:
        booking_date = datetime.strptime(data['booking_date'], '%Y-%m-%d').date()
        start_time = datetime.strptime(data['start_time'], '%H:%M').time()
        end_time = datetime.strptime(data['end_time'], '%H:%M').time()
    except ValueError:
        return jsonify({'error': 'Invalid date/time format'}), 400
    
    # Check if slot is available
    existing_booking = Booking.query.filter_by(
        artist_id=artist.id,
        booking_date=booking_date,
        status='confirmed'
    ).filter(
        db.or_(
            db.and_(Booking.start_time <= start_time, Booking.end_time > start_time),
            db.and_(Booking.start_time < end_time, Booking.end_time >= end_time)
        )
    ).first()
    
    if existing_booking:
        return jsonify({'error': 'Time slot already booked'}), 409
    
    # Calculate total price
    start_datetime = datetime.combine(booking_date, start_time)
    end_datetime = datetime.combine(booking_date, end_time)
    hours = (end_datetime - start_datetime).total_seconds() / 3600
    total_price = Decimal(str(hours)) * artist.price_per_hour
    
    # Create booking
    booking = Booking(
        customer_id=user_id,
        artist_id=artist.id,
        service_type=data['service_type'],
        booking_date=booking_date,
        start_time=start_time,
        end_time=end_time,
        total_price=total_price,
        notes=data.get('notes', '')
    )
    
    db.session.add(booking)
    db.session.commit()
    
    # Send confirmation email
    send_booking_confirmation_email.delay(booking.id)
    
    return jsonify({
        'message': 'Booking created successfully',
        'booking_id': booking.id,
        'total_price': str(total_price)
    }), 201

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
        'payment_status': booking.payment_status,
        'artist_name': f"{booking.artist.user.first_name} {booking.artist.user.last_name}" if booking.artist else None,
        'customer_name': f"{booking.customer.first_name} {booking.customer.last_name}" if booking.customer else None
    } for booking in bookings]), 200

@app.route('/api/bookings/<int:booking_id>/confirm', methods=['PUT'])
@jwt_required()
def confirm_booking(booking_id):
    user_id = get_jwt_identity()
    booking = Booking.query.get(booking_id)
    
    if not booking:
        return jsonify({'error': 'Booking not found'}), 404
    
    # Check if user is the artist
    artist = Artist.query.filter_by(user_id=user_id).first()
    if not artist or artist.id != booking.artist_id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    booking.status = 'confirmed'
    db.session.commit()
    
    return jsonify({'message': 'Booking confirmed successfully'}), 200

@app.route('/api/bookings/<int:booking_id>/cancel', methods=['PUT'])
@jwt_required()
def cancel_booking(booking_id):
    user_id = get_jwt_identity()
    booking = Booking.query.get(booking_id)
    
    if not booking:
        return jsonify({'error': 'Booking not found'}), 404
    
    # Check if user is the customer or artist
    if booking.customer_id != user_id:
        artist = Artist.query.filter_by(user_id=user_id).first()
        if not artist or artist.id != booking.artist_id:
            return jsonify({'error': 'Unauthorized'}), 403
    
    booking.status = 'cancelled'
    db.session.commit()
    
    return jsonify({'message': 'Booking cancelled successfully'}), 200

@app.route('/api/reviews', methods=['POST'])
@jwt_required()
def create_review():
    user_id = get_jwt_identity()
    data = request.get_json()
    
    required_fields = ['booking_id', 'rating']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing field: {field}'}), 400
    
    booking = Booking.query.get(data['booking_id'])
    if not booking or booking.customer_id != user_id:
        return jsonify({'error': 'Booking not found or unauthorized'}), 404
    
    if booking.status != 'completed':
        return jsonify({'error': 'Can only review completed bookings'}), 400
    
    review = Review(
        booking_id=booking.id,
        customer_id=user_id,
        artist_id=booking.artist_id,
        rating=data['rating'],
        comment=data.get('comment', '')
    )
    
    db.session.add(review)
    
    # Update artist rating
    artist = Artist.query.get(booking.artist_id)
    total_rating = artist.rating * artist.total_reviews + data['rating']
    artist.total_reviews += 1
    artist.rating = total_rating / artist.total_reviews
    
    db.session.commit()
    
    return jsonify({'message': 'Review created successfully'}), 201

@app.route('/api/artists/<int:artist_id>/reviews', methods=['GET'])
def get_artist_reviews(artist_id):
    reviews = Review.query.filter_by(artist_id=artist_id).order_by(Review.created_at.desc()).all()
    
    return jsonify([{
        'id': review.id,
        'rating': review.rating,
        'comment': review.comment,
        'customer_name': f"{review.customer.first_name} {review.customer.last_name}",
        'created_at': review.created_at.isoformat()
    } for review in reviews]), 200

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Resource not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)
