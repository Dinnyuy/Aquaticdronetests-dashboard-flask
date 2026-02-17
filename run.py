from flask import Flask, render_template, Response, jsonify, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from datetime import datetime, timedelta
import random
import time
# import serial  # <-- commented out: no real serial
# import serial.tools.list_ports
import threading
import logging
import os
import json
import cv2
import numpy as np
import pandas as pd
from sqlalchemy import func, and_
import io
import csv
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///water_monitoring.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your-secret-key-change-this-in-production'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Initialize extensions
db = SQLAlchemy(app)
migrate = Migrate(app, db)
bcrypt = Bcrypt(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('system.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('WaterMonitoringSystem')

# Thresholds for different systems
# Drone thresholds (Kribi waters specific)
DRONE_TURBIDITY_THRESHOLD = 41.0  # NTU
DRONE_TEMPERATURE_THRESHOLD = 32.0  # °C
DRONE_CONDUCTIVITY_THRESHOLD = 12.0  # mS/cm
DRONE_PH_THRESHOLD_LOW = 6.5
DRONE_PH_THRESHOLD_HIGH = 7.5
DRONE_DO_THRESHOLD = 5.0  # mg/L

# Buoy thresholds (kept for completeness but not actively used in drone dashboard)
BUOY_TURBIDITY_THRESHOLD = 10.0
BUOY_TEMPERATURE_THRESHOLD = 30.0
BUOY_CONDUCTIVITY_THRESHOLD = 10.0
BUOY_PH_THRESHOLD_LOW = 6.0
BUOY_PH_THRESHOLD_HIGH = 8.5
BUOY_PRESSURE_THRESHOLD = 2.0
BUOY_DO_THRESHOLD = 4.0
BUOY_WAVE_HEIGHT_THRESHOLD = 3.0
BUOY_CURRENT_SPEED_THRESHOLD = 2.0

ALERT_THRESHOLD = 3  # Minimum sensors that must exceed thresholds

# Serial Setup - completely disabled for simulation
drone_ser = None
buoy_ser = None
drone_connected = False
buoy_connected = False
drone_reconnect_attempts = 0
buoy_reconnect_attempts = 0
MAX_RECONNECT_ATTEMPTS = 50

# Camera Setup
camera = None
camera_initialized = False
camera_type = "none"
camera_lock = threading.Lock()

# Global variables for drone with thread locks
drone_data_lock = threading.Lock()
latest_drone_turbidity = 35.0          # initialised to avoid None
latest_drone_temperature = 28.0
latest_drone_conductivity = 10.0
latest_drone_ph = 7.0
latest_drone_do = 6.0
latest_drone_latitude = 4.2105
latest_drone_longitude = 6.4375
latest_drone_battery = 85.0
drone_last_update = datetime.now()
drone_connected = True  # Start as connected for simulation

# Global variables for buoy
latest_buoy_turbidity = 8.0
latest_buoy_temperature = 26.0
latest_buoy_conductivity = 8.0
latest_buoy_ph = 7.2
latest_buoy_do = 5.5
latest_buoy_pressure = 1.5
latest_buoy_wave_height = 0.5
latest_buoy_current_speed = 0.2
latest_buoy_air_temperature = 28.5
latest_buoy_wind_speed = 3.2
latest_buoy_solar_charging = 75.0
latest_buoy_battery = 92.0
latest_buoy_latitude = 4.2105
latest_buoy_longitude = 6.4375
buoy_last_update = datetime.now()

# Database models (kept for user authentication, but sensor data models are not actively used)
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, default=False)
    last_login = db.Column(db.DateTime)
    dashboard_preference = db.Column(db.String(20), default='drone')  # 'drone' or 'buoy'

# The following models are kept but not used in simulation mode.
# They are left here to avoid breaking references, but data_logger is disabled.
class DroneSensorData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    turbidity = db.Column(db.Float)
    temperature = db.Column(db.Float)
    conductivity = db.Column(db.Float)
    ph = db.Column(db.Float)
    do = db.Column(db.Float)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    battery = db.Column(db.Float)
    gps_type = db.Column(db.String(20))
    above_threshold = db.Column(db.Boolean, default=False)
    
    __table_args__ = (db.Index('idx_drone_timestamp', 'timestamp'),)
    
class BuoySensorData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    turbidity = db.Column(db.Float)
    temperature = db.Column(db.Float)
    conductivity = db.Column(db.Float)
    ph = db.Column(db.Float)
    do = db.Column(db.Float)
    pressure = db.Column(db.Float)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    gps_type = db.Column(db.String(20))
    above_threshold = db.Column(db.Boolean, default=False)
    
    __table_args__ = (db.Index('idx_buoy_timestamp', 'timestamp'),)

class SystemAlert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    system_type = db.Column(db.String(20))
    alert_type = db.Column(db.String(50))
    message = db.Column(db.Text)
    resolved = db.Column(db.Boolean, default=False)
    resolved_at = db.Column(db.DateTime)

class SystemLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    source = db.Column(db.String(50))
    level = db.Column(db.String(20))
    message = db.Column(db.Text)
    
    __table_args__ = (
        db.Index('idx_log_timestamp', 'timestamp'),
        db.Index('idx_log_source', 'source'),
        db.Index('idx_log_level', 'level'),
    )

class DataExport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    system_type = db.Column(db.String(20))
    start_date = db.Column(db.DateTime)
    end_date = db.Column(db.DateTime)
    format = db.Column(db.String(10))
    file_path = db.Column(db.String(255))
    
    user = db.relationship('User', backref=db.backref('exports', lazy=True))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Database logging is disabled in simulation mode – we only log to file/console.
def log_to_database(source, level, message):
    # This function is intentionally left empty to avoid database writes.
    # All logging goes to the file/console via logger.
    logger.info(f"[{source.upper()}] {level}: {message}")
    pass

# No database initialization needed for sensor data – we start with simulated values only.
def initialize_from_database():
    # Do nothing – we start with simulated values only.
    logger.info("Simulation mode: no database data loaded.")
    pass

# ----------------------------------------------------------------------
# Serial connection functions are completely disabled (no real hardware).
# ----------------------------------------------------------------------
def connect_to_drone():
    global drone_connected
    logger.info("Simulation mode: drone connection always on.")
    drone_connected = True
    return True

def connect_to_buoy():
    global buoy_connected
    logger.info("Simulation mode: buoy connection always on.")
    buoy_connected = True
    return True

# ----------------------------------------------------------------------
# Drone Data Simulator (unchanged, runs every 2 seconds)
# ----------------------------------------------------------------------
def drone_data_simulator():
    global latest_drone_turbidity, latest_drone_temperature, latest_drone_conductivity
    global latest_drone_ph, latest_drone_do, drone_connected, drone_last_update
    global latest_drone_latitude, latest_drone_longitude, latest_drone_battery
    
    drone_connected = True   # ensure connected status
    
    # Base values with realistic variations
    base_temperature = 28.0
    base_turbidity = 35.0
    base_conductivity = 10.0
    base_ph = 7.0
    base_do = 6.0
    
    # Ballast water simulation parameters
    ballast_event = False
    ballast_start_time = None
    ballast_duration = 0
    
    while True:
        try:
            with drone_data_lock:
                current_time = datetime.now()
                
                # Occasionally simulate ballast water events (10% chance every 30 seconds)
                if random.random() < 0.1 and not ballast_event:
                    ballast_event = True
                    ballast_start_time = current_time
                    ballast_duration = random.randint(30, 180)
                    logger.info("Simulating ballast water event")
                
                if ballast_event:
                    time_since_start = (current_time - ballast_start_time).total_seconds()
                    if time_since_start < ballast_duration:
                        progress = time_since_start / ballast_duration
                        intensity = 4 * progress * (1 - progress)
                        
                        latest_drone_temperature = base_temperature + 4 * intensity + random.uniform(-0.5, 0.5)
                        latest_drone_turbidity = base_turbidity + 20 * intensity + random.uniform(-2, 2)
                        latest_drone_conductivity = base_conductivity + 6 * intensity + random.uniform(-0.3, 0.3)
                        latest_drone_ph = base_ph + (0.8 * intensity if random.random() > 0.5 else -0.8 * intensity) + random.uniform(-0.1, 0.1)
                        latest_drone_do = base_do - 2 * intensity + random.uniform(-0.2, 0.2)
                    else:
                        ballast_event = False
                        ballast_start_time = None
                        logger.info("Ballast water event ended")
                
                if not ballast_event:
                    latest_drone_temperature = base_temperature + random.uniform(-2, 2) + 0.5 * np.sin(time.time() / 300)
                    latest_drone_turbidity = max(0, base_turbidity + random.uniform(-5, 5) + 2 * np.sin(time.time() / 600))
                    latest_drone_conductivity = base_conductivity + random.uniform(-1, 1) + 0.3 * np.sin(time.time() / 450)
                    latest_drone_ph = base_ph + random.uniform(-0.2, 0.2) + 0.1 * np.sin(time.time() / 900)
                    latest_drone_do = base_do + random.uniform(-0.5, 0.5) + 0.2 * np.sin(time.time() / 720)
                
                # Ensure values are within reasonable bounds
                latest_drone_temperature = max(20, min(36, latest_drone_temperature))
                latest_drone_turbidity = max(0, min(60, latest_drone_turbidity))
                latest_drone_conductivity = max(5, min(18, latest_drone_conductivity))
                latest_drone_ph = max(6.0, min(8.2, latest_drone_ph))
                latest_drone_do = max(3.0, min(10.0, latest_drone_do))
                
                # Simulate drone movement
                center_lat = 4.2105
                center_lon = 6.4375
                lat_offset = (random.random() - 0.5) * 0.0002
                lon_offset = (random.random() - 0.5) * 0.0002
                lat_return = (center_lat - latest_drone_latitude) * 0.01
                lon_return = (center_lon - latest_drone_longitude) * 0.01
                
                latest_drone_latitude += lat_offset + lat_return
                latest_drone_longitude += lon_offset + lon_return
                
                latest_drone_latitude = max(center_lat - 0.001, min(center_lat + 0.001, latest_drone_latitude))
                latest_drone_longitude = max(center_lon - 0.001, min(center_lon + 0.001, latest_drone_longitude))
                
                # Simulate battery drain
                battery_drain = 0.005 if ballast_event else 0.003
                latest_drone_battery = max(10, latest_drone_battery - battery_drain)
                if latest_drone_battery < 20 and random.random() < 0.05:
                    latest_drone_battery = min(100, latest_drone_battery + 30)
                    logger.info("Drone battery recharged")
                
                drone_last_update = current_time
                drone_connected = True
                
                if int(time.time()) % 60 == 0:
                    logger.info(f"Drone data updated: Temp={latest_drone_temperature:.1f}°C, Turb={latest_drone_turbidity:.1f}NTU, Battery={latest_drone_battery:.1f}%")
                
        except Exception as e:
            logger.error(f"Drone simulator error: {str(e)}")
        
        time.sleep(2)

# ----------------------------------------------------------------------
# Buoy data simulator (unchanged, runs every 3 seconds)
# ----------------------------------------------------------------------
def buoy_data_simulator():
    global latest_buoy_turbidity, latest_buoy_temperature, latest_buoy_conductivity
    global latest_buoy_ph, latest_buoy_do, latest_buoy_pressure, buoy_last_update
    global latest_buoy_wave_height, latest_buoy_current_speed
    global latest_buoy_air_temperature, latest_buoy_wind_speed
    global latest_buoy_solar_charging, latest_buoy_battery, buoy_connected
    
    buoy_connected = True
    
    base_temperature = 26.0
    base_turbidity = 8.0
    base_conductivity = 8.0
    base_ph = 7.2
    base_do = 5.5
    base_pressure = 1.5
    
    while True:
        try:
            current_time = datetime.now()
            tidal_factor = np.sin(time.time() / 22320)
            hour = current_time.hour
            diurnal_factor = np.sin((hour - 12) * np.pi / 12)
            
            latest_buoy_temperature = base_temperature + diurnal_factor * 2 + random.uniform(-0.5, 0.5)
            latest_buoy_turbidity = max(0, base_turbidity + abs(tidal_factor) * 3 + random.uniform(-1, 1))
            latest_buoy_conductivity = base_conductivity + tidal_factor * 1 + random.uniform(-0.2, 0.2)
            latest_buoy_ph = base_ph + 0.1 * diurnal_factor + random.uniform(-0.05, 0.05)
            latest_buoy_do = base_do + diurnal_factor * 0.5 + random.uniform(-0.1, 0.1)
            latest_buoy_pressure = base_pressure + abs(tidal_factor) * 0.5 + random.uniform(-0.1, 0.1)
            
            latest_buoy_wave_height = 0.5 + abs(tidal_factor) * 1.0 + random.uniform(0, 0.3)
            latest_buoy_current_speed = 0.3 + abs(tidal_factor) * 0.5 + random.uniform(0, 0.2)
            latest_buoy_air_temperature = base_temperature + diurnal_factor * 3 + random.uniform(-1, 1)
            latest_buoy_wind_speed = 3.0 + random.uniform(0, 4) + abs(diurnal_factor) * 2
            
            if 6 <= hour <= 18:
                solar_efficiency = 50 * (1 - abs(hour - 12) / 6)
            else:
                solar_efficiency = 0
            latest_buoy_solar_charging = solar_efficiency
            
            if solar_efficiency > 0:
                battery_change = (solar_efficiency / 100) * 0.1
            else:
                battery_change = -0.002
            latest_buoy_battery = max(0, min(100, latest_buoy_battery + battery_change))
            
            buoy_last_update = current_time
            buoy_connected = True
            
        except Exception as e:
            logger.error(f"Buoy simulator error: {str(e)}")
        
        time.sleep(3)

# ----------------------------------------------------------------------
# Data logger thread – DISABLED in simulation mode
# ----------------------------------------------------------------------
def data_logger():
    # This function is intentionally left empty – no database writes.
    pass

# ----------------------------------------------------------------------
# Cleanup scheduler – DISABLED
# ----------------------------------------------------------------------
def cleanup_scheduler():
    pass

# ----------------------------------------------------------------------
# Camera functions (unchanged, fully working)
# ----------------------------------------------------------------------
def init_camera():
    global camera, camera_initialized, camera_type
    
    camera_initialized = False
    camera = None
    camera_type = "none"
    
    # Method 1: Try Raspberry Pi Camera (picamera2)
    try:
        from picamera2 import Picamera2
        camera = Picamera2()
        config = camera.create_video_configuration(main={"size": (640, 480)})
        camera.configure(config)
        camera.start()
        time.sleep(2)
        camera_initialized = True
        camera_type = "picamera2"
        logger.info("✅ Raspberry Pi Camera (picamera2) initialized successfully")
        log_to_database('camera', 'info', 'Raspberry Pi Camera initialized')
        return True
    except ImportError:
        logger.info("picamera2 not available")
    except Exception as e:
        logger.warning(f"picamera2 failed: {str(e)}")
    
    # Method 2: Try Legacy Raspberry Pi Camera (picamera)
    try:
        import picamera
        camera = picamera.PiCamera()
        camera.resolution = (640, 480)
        camera.framerate = 20
        time.sleep(2)
        camera_initialized = True
        camera_type = "picamera"
        logger.info("✅ Legacy Raspberry Pi Camera (picamera) initialized successfully")
        log_to_database('camera', 'info', 'Legacy Pi Camera initialized')
        return True
    except ImportError:
        logger.info("picamera not available")
    except Exception as e:
        logger.warning(f"picamera failed: {str(e)}")
    
    # Method 3: Try USB Webcam with OpenCV
    logger.info("Attempting to initialize USB Webcam...")
    camera_indices = [0, 1, 2, 3, 4]
    for i in camera_indices:
        try:
            logger.info(f"Trying camera index {i}")
            camera = cv2.VideoCapture(i)
            if camera.isOpened():
                ret, frame = camera.read()
                if ret and frame is not None:
                    logger.info(f"✅ USB Webcam (index {i}) initialized successfully")
                    camera_initialized = True
                    camera_type = f"usb_webcam_{i}"
                    log_to_database('camera', 'info', f'USB Webcam {i} initialized')
                    return True
                else:
                    logger.warning(f"Camera index {i} opened but cannot read frames")
                    camera.release()
                    camera = None
            else:
                logger.info(f"Camera index {i} not available")
        except Exception as e:
            logger.warning(f"USB Webcam index {i} failed: {str(e)}")
            if camera:
                camera.release()
                camera = None
    
    # If all methods fail, use dummy camera
    logger.warning("❌ All camera initialization methods failed, using dummy camera")
    camera_type = "dummy"
    camera_initialized = True
    log_to_database('camera', 'warning', 'Using dummy camera (no real camera found)')
    return True

def generate_camera_frame():
    global camera, camera_initialized, camera_type
    
    try:
        if camera_type == "dummy" or not camera_initialized:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            for y in range(480):
                color = int(50 + (y / 480) * 50)
                cv2.line(frame, (0, y), (640, y), (color, color, 100), 1)
            cv2.putText(frame, "DRONE CAMERA FEED", (120, 150), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(frame, "Camera Initializing...", (200, 200), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cv2.putText(frame, timestamp, (220, 250), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)
            cv2.putText(frame, "Status: Dummy Mode", (240, 280), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 200, 255), 1)
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            return buffer.tobytes()
        
        elif "usb_webcam" in camera_type and hasattr(camera, 'read'):
            with camera_lock:
                success, frame = camera.read()
            if success and frame is not None:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cv2.putText(frame, f"Drone Camera - {timestamp}", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                cv2.putText(frame, f"GPS: {latest_drone_latitude:.6f}, {latest_drone_longitude:.6f}", 
                           (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                return buffer.tobytes()
            else:
                return generate_camera_frame()
        else:
            return generate_camera_frame()
            
    except Exception as e:
        logger.error(f"Camera frame error: {str(e)}")
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(frame, "CAMERA ERROR", (200, 240), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        cv2.putText(frame, str(e)[:50], (100, 280), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        ret, buffer = cv2.imencode('.jpg', frame)
        return buffer.tobytes()

def generate_frames():
    while True:
        try:
            frame_bytes = generate_camera_frame()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        except Exception as e:
            logger.error(f"Frame generation error: {str(e)}")
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(frame, "STREAM ERROR", (200, 240), 
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            ret, buffer = cv2.imencode('.jpg', frame)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        time.sleep(0.033)

# ----------------------------------------------------------------------
# Helper functions for statistics (unchanged)
# ----------------------------------------------------------------------
def generate_statistics(system_type, days=30):
    # In simulation mode, we return empty stats (or could generate dummy)
    return None

def generate_buoy_statistics(days=30):
    return None

# ----------------------------------------------------------------------
# All routes remain exactly the same (they return simulated data)
# ----------------------------------------------------------------------

# Authentication Routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        dashboard_type = session.get('dashboard_type', current_user.dashboard_preference)
        if dashboard_type == 'buoy':
            return redirect(url_for('buoy_dashboard'))
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        dashboard_type = session.get('dashboard_type', current_user.dashboard_preference)
        if dashboard_type == 'buoy':
            return redirect(url_for('buoy_dashboard'))
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        dashboard_type = request.form.get('dashboard_type', 'drone')
        remember = True if request.form.get('remember') else False
        
        user = User.query.filter_by(username=username).first()
        
        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user, remember=remember)
            user.last_login = datetime.utcnow()
            session['dashboard_type'] = dashboard_type
            db.session.commit()
            flash('Login successful!', 'success')
            log_to_database('system', 'info', f'User {username} logged in')
            
            if dashboard_type == 'buoy':
                return redirect(url_for('buoy_dashboard'))
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
            log_to_database('system', 'warning', f'Failed login attempt for {username}')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        dashboard_preference = request.form.get('dashboard_preference', 'drone')
        
        if password != confirm_password:
            flash('Passwords do not match', 'danger')
        elif len(password) < 6:
            flash('Password must be at least 6 characters long', 'danger')
        elif User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
        elif User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
        else:
            hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
            user = User(
                username=username, 
                email=email, 
                password_hash=hashed_password,
                dashboard_preference=dashboard_preference
            )
            db.session.add(user)
            db.session.commit()
            flash('Account created successfully! Please log in.', 'success')
            log_to_database('system', 'info', f'New user registered: {username}')
            return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    log_to_database('system', 'info', f'User {current_user.username} logged out')
    logout_user()
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))

@app.route('/switch-dashboard/<dashboard_type>')
@login_required
def switch_dashboard(dashboard_type):
    if dashboard_type in ['drone', 'buoy']:
        session['dashboard_type'] = dashboard_type
        current_user.dashboard_preference = dashboard_type
        db.session.commit()
        log_to_database('system', 'info', f'User switched to {dashboard_type} dashboard')
        
        if dashboard_type == 'buoy':
            return redirect(url_for('buoy_dashboard'))
        return redirect(url_for('dashboard'))
    
    flash('Invalid dashboard type', 'danger')
    return redirect(url_for('dashboard'))

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

# Drone Dashboard Routes
@app.route('/api/drone/real-time')
@login_required
def get_drone_real_time_data():
    with drone_data_lock:
        data = {
            'turbidity': latest_drone_turbidity,
            'temperature': latest_drone_temperature,
            'conductivity': latest_drone_conductivity,
            'ph': latest_drone_ph,
            'do': latest_drone_do,
            'latitude': latest_drone_latitude,
            'longitude': latest_drone_longitude,
            'battery': latest_drone_battery,
            'timestamp': drone_last_update.isoformat() if drone_last_update else None,
            'connected': drone_connected,
            'thresholds': {
                'turbidity': DRONE_TURBIDITY_THRESHOLD,
                'temperature': DRONE_TEMPERATURE_THRESHOLD,
                'conductivity': DRONE_CONDUCTIVITY_THRESHOLD,
                'ph_low': DRONE_PH_THRESHOLD_LOW,
                'ph_high': DRONE_PH_THRESHOLD_HIGH,
                'dissolved_oxygen': DRONE_DO_THRESHOLD
            }
        }
    return jsonify(data)

@app.route('/api/drone/historical')
@login_required
def get_drone_historical_data():
    # In simulation, return empty list (frontend uses its own simulatedData)
    return jsonify([])

@app.route('/api/drone/statistics')
@login_required
def get_drone_statistics():
    return jsonify({})

@app.route('/api/drone/distribution')
@login_required
def get_drone_distribution_data():
    return jsonify({})

# Buoy Dashboard Routes
@app.route('/buoy-dashboard')
@login_required
def buoy_dashboard():
    return render_template('buoy_dashboard.html')

@app.route('/api/buoy/real-time')
@login_required
def get_buoy_real_time_data():
    return jsonify({
        'turbidity': latest_buoy_turbidity,
        'temperature': latest_buoy_temperature,
        'conductivity': latest_buoy_conductivity,
        'ph': latest_buoy_ph,
        'do': latest_buoy_do,
        'pressure': latest_buoy_pressure,
        'wave_height': latest_buoy_wave_height,
        'current_speed': latest_buoy_current_speed,
        'air_temperature': latest_buoy_air_temperature,
        'wind_speed': latest_buoy_wind_speed,
        'solar_charging': latest_buoy_solar_charging,
        'battery': latest_buoy_battery,
        'latitude': latest_buoy_latitude,
        'longitude': latest_buoy_longitude,
        'timestamp': buoy_last_update.isoformat() if buoy_last_update else None,
        'connected': buoy_connected,
        'thresholds': {
            'turbidity': BUOY_TURBIDITY_THRESHOLD,
            'temperature': BUOY_TEMPERATURE_THRESHOLD,
            'conductivity': BUOY_CONDUCTIVITY_THRESHOLD,
            'ph_low': BUOY_PH_THRESHOLD_LOW,
            'ph_high': BUOY_PH_THRESHOLD_HIGH,
            'dissolved_oxygen': BUOY_DO_THRESHOLD,
            'pressure': BUOY_PRESSURE_THRESHOLD,
            'wave_height': BUOY_WAVE_HEIGHT_THRESHOLD,
            'current_speed': BUOY_CURRENT_SPEED_THRESHOLD
        }
    })

@app.route('/api/buoy/historical')
@login_required
def get_buoy_historical_data():
    return jsonify([])

@app.route('/api/buoy/statistics')
@login_required
def get_buoy_statistics():
    return jsonify({})

# Debug & System Routes
@app.route('/api/debug/serial-status')
@login_required
def get_serial_debug_info():
    return jsonify({
        'drone': {
            'connected': drone_connected,
            'reconnect_attempts': 0,
            'port': 'SIMULATED',
            'last_update': drone_last_update.isoformat() if drone_last_update else None
        },
        'buoy': {
            'connected': buoy_connected,
            'reconnect_attempts': 0,
            'port': 'SIMULATED',
            'last_update': buoy_last_update.isoformat() if buoy_last_update else None
        },
        'available_ports': ['SIMULATED']
    })

@app.route('/api/debug/camera-status')
@login_required
def get_camera_status():
    return jsonify({
        'initialized': camera_initialized,
        'type': camera_type,
        'status': 'running' if camera_initialized else 'error'
    })

@app.route('/api/debug/reconnect-drone')
@login_required
def reconnect_drone():
    global drone_connected
    drone_connected = True
    return jsonify({'status': 'success', 'message': 'Drone reconnected (simulated)'})

@app.route('/api/debug/reconnect-buoy')
@login_required
def reconnect_buoy():
    global buoy_connected
    buoy_connected = True
    return jsonify({'status': 'success', 'message': 'Buoy reconnected (simulated)'})

@app.route('/api/debug/reconnect-camera')
@login_required
def reconnect_camera():
    success = init_camera()
    if success:
        return jsonify({'status': 'success', 'message': f'Camera reconnected: {camera_type}'})
    else:
        return jsonify({'status': 'error', 'message': 'Failed to reconnect camera'})

@app.route('/api/debug/system-logs')
@login_required
def get_system_logs():
    # Return dummy logs
    logs = []
    return jsonify(logs)

# Camera Routes
@app.route('/video_feed')
@login_required
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/camera/snapshot')
@login_required
def take_snapshot_api():
    try:
        frame_bytes = generate_camera_frame()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"snapshot_{timestamp}.jpg"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        with open(filepath, 'wb') as f:
            f.write(frame_bytes)
        log_to_database('camera', 'info', f'Snapshot saved: {filename}')
        return jsonify({
            'status': 'success',
            'filename': filename,
            'url': f'/static/uploads/{filename}'
        })
    except Exception as e:
        logger.error(f"Snapshot error: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)})

# Export Routes
@app.route('/api/export/drone-data')
@login_required
def export_drone_data():
    # Return empty CSV with headers
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Timestamp', 'Temperature (°C)', 'Turbidity (NTU)', 
                    'Conductivity (mS/cm)', 'pH', 'Dissolved Oxygen (mg/L)',
                    'Latitude', 'Longitude', 'Battery (%)', 'GPS Type', 'Alert'])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=drone_data_simulated.csv'}
    )

@app.route('/api/export/buoy-data')
@login_required
def export_buoy_data():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Timestamp', 'Temperature (°C)', 'Turbidity (NTU)', 
                    'Conductivity (mS/cm)', 'pH', 'Dissolved Oxygen (mg/L)',
                    'Pressure (bar)', 'Latitude', 'Longitude', 'GPS Type', 'Alert'])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=buoy_data_simulated.csv'}
    )

# Alerts API
@app.route('/api/alerts')
@login_required
def get_alerts():
    return jsonify([])

@app.route('/api/alerts/<int:alert_id>/resolve', methods=['POST'])
@login_required
def resolve_alert(alert_id):
    return jsonify({'status': 'success'})

# System status
@app.route('/api/system-status')
@login_required
def get_system_status():
    return jsonify({
        'drone': {
            'connected': drone_connected,
            'last_update': drone_last_update.isoformat() if drone_last_update else None,
            'camera': camera_initialized
        },
        'buoy': {
            'connected': buoy_connected,
            'last_update': buoy_last_update.isoformat() if buoy_last_update else None
        },
        'database': {
            'drone_records': 0,
            'buoy_records': 0,
            'alerts': 0
        }
    })

# ----------------------------------------------------------------------
# Background service starter (ensures simulators run)
# ----------------------------------------------------------------------
def start_background_services():
    """Initialize camera and start background simulator threads."""
    global camera_initialized, camera_type

    # Initialize camera
    camera_init_success = init_camera()
    if camera_init_success:
        logger.info(f"Camera initialized: {camera_type}")
    else:
        logger.warning("Camera initialization failed")

    # Start background simulation threads
    threading.Thread(target=drone_data_simulator, daemon=True).start()
    threading.Thread(target=buoy_data_simulator, daemon=True).start()
    # Data logger and cleanup threads are disabled in simulation mode.
    # threading.Thread(target=data_logger, daemon=True).start()
    # threading.Thread(target=cleanup_scheduler, daemon=True).start()

if __name__ == '__main__':
    with app.app_context():
        # Create tables only for User (authentication)
        db.create_all()
        
        # Create default admin user if none exists
        if not User.query.filter_by(username='admin').first():
            hashed_password = bcrypt.generate_password_hash('admin123').decode('utf-8')
            admin_user = User(
                username='admin',
                email='admin@atawi.com',
                password_hash=hashed_password,
                is_admin=True
            )
            db.session.add(admin_user)
            db.session.commit()
            logger.info("Default admin user created")
        
        # No database initialization for sensor data
        logger.info("Simulation mode: no sensor data loaded from database.")
    
    # Start background services
    start_background_services()
    
    # Run the Flask development server
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)