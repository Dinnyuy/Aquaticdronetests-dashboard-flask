from flask import Flask, render_template, Response, jsonify, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from datetime import datetime, timedelta
from run_simulation import start_simulation, get_latest_drone_data, get_latest_buoy_data
import random
import time
import serial
import threading
import logging
import os
import serial.tools.list_ports
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

# Buoy thresholds
BUOY_TURBIDITY_THRESHOLD = 10.0  # NTU
BUOY_TEMPERATURE_THRESHOLD = 30.0  # °C
BUOY_CONDUCTIVITY_THRESHOLD = 10.0  # mS/cm
BUOY_PH_THRESHOLD_LOW = 6.0
BUOY_PH_THRESHOLD_HIGH = 8.5
BUOY_PRESSURE_THRESHOLD = 2.0  # bar
BUOY_DO_THRESHOLD = 4.0  # mg/L
BUOY_WAVE_HEIGHT_THRESHOLD = 3.0  # meters
BUOY_CURRENT_SPEED_THRESHOLD = 2.0  # m/s

ALERT_THRESHOLD = 3  # Minimum sensors that must exceed thresholds

# Serial Setup
DRONE_SERIAL_PORT = 'COM4'
BUOY_SERIAL_PORT = 'COM5'
BAUD_RATE = 9600
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

# Global variables for drone
latest_drone_turbidity = None
latest_drone_temperature = None
latest_drone_conductivity = None
latest_drone_ph = None
latest_drone_do = None
latest_drone_latitude = 4.2105
latest_drone_longitude = 6.4375
latest_drone_battery = 85.0
drone_last_update = None

# Global variables for buoy
latest_buoy_turbidity = None
latest_buoy_temperature = None
latest_buoy_conductivity = None
latest_buoy_ph = None
latest_buoy_do = None
latest_buoy_pressure = None
latest_buoy_wave_height = 0.5
latest_buoy_current_speed = 0.2
latest_buoy_air_temperature = 28.5
latest_buoy_wind_speed = 3.2
latest_buoy_solar_charging = 75.0
latest_buoy_battery = 92.0
latest_buoy_latitude = 4.2105
latest_buoy_longitude = 6.4375
buoy_last_update = None

# Database models
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, default=False)
    last_login = db.Column(db.DateTime)
    dashboard_preference = db.Column(db.String(20), default='drone')  # 'drone' or 'buoy'

class DroneSensorData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    turbidity = db.Column(db.Float)
    temperature = db.Column(db.Float)
    conductivity = db.Column(db.Float)
    ph = db.Column(db.Float)
    do = db.Column(db.Float)  # Dissolved Oxygen
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    battery = db.Column(db.Float)
    gps_type = db.Column(db.String(20))
    above_threshold = db.Column(db.Boolean, default=False)
    
    # Index for faster queries
    __table_args__ = (
        db.Index('idx_drone_timestamp', 'timestamp'),
    )
    
class BuoySensorData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    turbidity = db.Column(db.Float)
    temperature = db.Column(db.Float)
    conductivity = db.Column(db.Float)
    ph = db.Column(db.Float)
    do = db.Column(db.Float)  # Dissolved Oxygen
    pressure = db.Column(db.Float)  # Water pressure
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    gps_type = db.Column(db.String(20))
    above_threshold = db.Column(db.Boolean, default=False)
    
    # Index for faster queries
    __table_args__ = (
        db.Index('idx_buoy_timestamp', 'timestamp'),
    )

class SystemAlert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    system_type = db.Column(db.String(20))  # 'drone' or 'buoy'
    alert_type = db.Column(db.String(50))
    message = db.Column(db.Text)
    resolved = db.Column(db.Boolean, default=False)
    resolved_at = db.Column(db.DateTime)

class SystemLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    source = db.Column(db.String(50))  # 'drone', 'buoy', 'camera', 'system'
    level = db.Column(db.String(20))  # 'info', 'warning', 'error', 'critical'
    message = db.Column(db.Text)
    
    # Index for faster queries
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

def log_to_database(source, level, message):
    """Log message to database"""
    try:
        log_entry = SystemLog(
            source=source,
            level=level,
            message=message[:500]  # Limit message length
        )
        db.session.add(log_entry)
        db.session.commit()
    except Exception as e:
        logger.error(f"Failed to log to database: {str(e)}")

# Initialize from database
def initialize_from_database():
    global latest_drone_turbidity, latest_drone_temperature, latest_drone_conductivity
    global latest_drone_ph, latest_drone_do
    global latest_buoy_turbidity, latest_buoy_temperature, latest_buoy_conductivity
    global latest_buoy_ph, latest_buoy_do, latest_buoy_pressure
    global drone_last_update, buoy_last_update
    
    try:
        # Get last drone data
        last_drone_entry = DroneSensorData.query.order_by(DroneSensorData.timestamp.desc()).first()
        if last_drone_entry:
            latest_drone_turbidity = last_drone_entry.turbidity
            latest_drone_temperature = last_drone_entry.temperature
            latest_drone_conductivity = last_drone_entry.conductivity
            latest_drone_ph = last_drone_entry.ph
            latest_drone_do = last_drone_entry.do
            drone_last_update = last_drone_entry.timestamp
            
        # Get last buoy data
        last_buoy_entry = BuoySensorData.query.order_by(BuoySensorData.timestamp.desc()).first()
        if last_buoy_entry:
            latest_buoy_turbidity = last_buoy_entry.turbidity
            latest_buoy_temperature = last_buoy_entry.temperature
            latest_buoy_conductivity = last_buoy_entry.conductivity
            latest_buoy_ph = last_buoy_entry.ph
            latest_buoy_do = last_buoy_entry.do
            latest_buoy_pressure = last_buoy_entry.pressure
            buoy_last_update = last_buoy_entry.timestamp
            
        logger.info("Initialized from database successfully")
        log_to_database('system', 'info', 'System initialized from database')
    except Exception as e:
        logger.error(f"Database init error: {str(e)}")
        log_to_database('system', 'error', f'Database init error: {str(e)}')
        try:
            db.drop_all()
            db.create_all()
        except Exception as e2:
            logger.error(f"Failed to recreate database: {str(e2)}")

# Connect to drone serial
def connect_to_drone():
    global drone_ser, drone_connected, drone_reconnect_attempts
    
    try:
        if drone_ser and drone_ser.is_open:
            drone_ser.close()
            
        ports = list(serial.tools.list_ports.comports())
        logger.info(f"Available ports: {[p.device for p in ports]}")
        
        # Try to find the drone port
        drone_port = None
        for port in ports:
            if 'Arduino' in port.description or 'USB' in port.description:
                drone_port = port.device
                break
        
        if not drone_port and ports:
            drone_port = ports[0].device
            
        if not drone_port:
            drone_port = DRONE_SERIAL_PORT
            
        drone_ser = serial.Serial(drone_port, BAUD_RATE, timeout=1)
        drone_connected = True
        drone_reconnect_attempts = 0
        logger.info(f"Connected to drone on {drone_port}")
        drone_ser.flushInput()
        log_to_database('drone', 'info', f'Connected to drone on {drone_port}')
        return True
    except Exception as e:
        logger.error(f"Drone connection error: {str(e)}")
        drone_ser = None
        drone_connected = False
        drone_reconnect_attempts += 1
        log_to_database('drone', 'error', f'Connection failed: {str(e)}')
        return False

# Connect to buoy serial
def connect_to_buoy():
    global buoy_ser, buoy_connected, buoy_reconnect_attempts
    
    try:
        if buoy_ser and buoy_ser.is_open:
            buoy_ser.close()
            
        ports = list(serial.tools.list_ports.comports())
        
        # Try to find the buoy port (different from drone)
        buoy_port = None
        for port in ports:
            if 'COM5' in port.device or 'ttyUSB' in port.device:
                buoy_port = port.device
                break
        
        if not buoy_port:
            buoy_port = BUOY_SERIAL_PORT
            
        buoy_ser = serial.Serial(buoy_port, BAUD_RATE, timeout=1)
        buoy_connected = True
        buoy_reconnect_attempts = 0
        logger.info(f"Connected to buoy on {buoy_port}")
        buoy_ser.flushInput()
        log_to_database('buoy', 'info', f'Connected to buoy on {buoy_port}')
        return True
    except Exception as e:
        logger.error(f"Buoy connection error: {str(e)}")
        buoy_ser = None
        buoy_connected = False
        buoy_reconnect_attempts += 1
        log_to_database('buoy', 'error', f'Connection failed: {str(e)}')
        return False

# Drone serial reader
def drone_serial_reader():
    global latest_drone_turbidity, latest_drone_temperature, latest_drone_conductivity
    global latest_drone_ph, latest_drone_do, drone_connected, drone_last_update
    global latest_drone_latitude, latest_drone_longitude, latest_drone_battery
    
    while True:
        if not drone_connected:
            if drone_reconnect_attempts < MAX_RECONNECT_ATTEMPTS:
                logger.info(f"Drone reconnect attempt {drone_reconnect_attempts+1}/{MAX_RECONNECT_ATTEMPTS}")
                if connect_to_drone():
                    time.sleep(1)
                    continue
                else:
                    time.sleep(5)
                    continue
            else:
                time.sleep(10)
                continue
        
        if drone_ser and drone_ser.is_open:
            try:
                # Try to read from serial
                if drone_ser.in_waiting > 0:
                    line = drone_ser.readline().decode('utf-8', errors='ignore').strip()
                    
                    if line:
                        logger.debug(f"Drone raw data: {line}")
                        
                        # Parse the data (adjust based on your Arduino format)
                        if "|" in line:
                            parts = line.split("|")
                            values = {}
                            
                            for part in parts:
                                key_val = part.strip().split(":")
                                if len(key_val) >= 2:
                                    key = key_val[0].strip().upper()
                                    val = ":".join(key_val[1:]).strip()
                                    clean_val = ''.join([c for c in val if c in '0123456789.-'])
                                    
                                    if key == "TEMP" and clean_val:
                                        values["temperature"] = float(clean_val)
                                    elif key == "TURB" and clean_val:
                                        values["turbidity"] = float(clean_val)
                                    elif key == "EC" and clean_val:
                                        values["conductivity"] = float(clean_val)
                                    elif key == "PH" and clean_val:
                                        values["ph"] = float(clean_val)
                                    elif key == "DO" and clean_val:
                                        values["do"] = float(clean_val)
                                    elif key == "LAT" and clean_val:
                                        values["latitude"] = float(clean_val)
                                    elif key == "LON" and clean_val:
                                        values["longitude"] = float(clean_val)
                                    elif key == "BAT" and clean_val:
                                        values["battery"] = float(clean_val)
                            
                            # Update global variables
                            if values:
                                if "temperature" in values:
                                    latest_drone_temperature = values["temperature"]
                                if "turbidity" in values:
                                    latest_drone_turbidity = values["turbidity"]
                                if "conductivity" in values:
                                    latest_drone_conductivity = values["conductivity"]
                                if "ph" in values:
                                    latest_drone_ph = values["ph"]
                                if "do" in values:
                                    latest_drone_do = values["do"]
                                if "latitude" in values:
                                    latest_drone_latitude = values["latitude"]
                                if "longitude" in values:
                                    latest_drone_longitude = values["longitude"]
                                if "battery" in values:
                                    latest_drone_battery = values["battery"]
                                
                                drone_last_update = datetime.now()
                                logger.info(f"Drone updated: Temp={latest_drone_temperature}, Turb={latest_drone_turbidity}")
                
                # If no data from serial, simulate data for testing
                else:
                    time.sleep(2)  # Wait before simulating
                    
                    # Simulate drone movement and sensor readings
                    latest_drone_turbidity = round(random.uniform(0, 50), 2)
                    latest_drone_temperature = round(random.uniform(20, 35), 2)
                    latest_drone_conductivity = round(random.uniform(5, 15), 2)
                    latest_drone_ph = round(random.uniform(6.0, 8.0), 2)
                    latest_drone_do = round(random.uniform(4, 10), 2)
                    latest_drone_battery = max(0, min(100, latest_drone_battery - 0.01))
                    
                    # Simulate drone movement (random walk)
                    lat_offset = (random.random() - 0.5) * 0.001
                    lon_offset = (random.random() - 0.5) * 0.001
                    latest_drone_latitude = max(4.2, min(4.22, latest_drone_latitude + lat_offset))
                    latest_drone_longitude = max(6.43, min(6.45, latest_drone_longitude + lon_offset))
                    
                    drone_last_update = datetime.now()
                    
            except Exception as e:
                logger.error(f"Drone serial error: {str(e)}")
                drone_connected = False
                drone_ser = None
                log_to_database('drone', 'error', f'Serial error: {str(e)}')
        else:
            drone_connected = False
        
        time.sleep(0.1)

# Buoy serial reader (simulated for now)
def buoy_serial_reader():
    global latest_buoy_turbidity, latest_buoy_temperature, latest_buoy_conductivity
    global latest_buoy_ph, latest_buoy_do, latest_buoy_pressure, buoy_connected, buoy_last_update
    
    while True:
        # Simulate buoy data (stationary)
        if random.random() > 0.3:
            latest_buoy_turbidity = round(random.uniform(0, 20), 2)
            latest_buoy_temperature = round(random.uniform(15, 30), 2)
            latest_buoy_conductivity = round(random.uniform(1, 12), 2)
            latest_buoy_ph = round(random.uniform(6.5, 8.5), 2)
            latest_buoy_do = round(random.uniform(3, 9), 2)
            latest_buoy_pressure = round(random.uniform(0.5, 3.0), 2)
            buoy_last_update = datetime.now()
            
        time.sleep(3)

# Buoy wave and weather simulator
def buoy_wave_simulator():
    """Simulate buoy wave and weather data"""
    global latest_buoy_wave_height, latest_buoy_current_speed
    global latest_buoy_air_temperature, latest_buoy_wind_speed
    global latest_buoy_solar_charging, latest_buoy_battery
    
    while True:
        # Simulate wave data
        latest_buoy_wave_height = round(random.uniform(0.2, 2.5), 2)
        latest_buoy_current_speed = round(random.uniform(0.1, 1.5), 2)
        
        # Simulate weather data
        latest_buoy_air_temperature = round(random.uniform(25, 32), 2)
        latest_buoy_wind_speed = round(random.uniform(1, 8), 2)
        
        # Simulate solar charging
        hour = datetime.now().hour
        if 6 <= hour <= 18:  # Daytime
            solar_efficiency = max(0, min(100, 50 + (hour - 12) * 5))
        else:
            solar_efficiency = 0
        
        latest_buoy_solar_charging = round(solar_efficiency, 1)
        
        # Simulate battery draining/charging
        battery_change = (solar_efficiency / 100) - 0.01  # Charge during day, discharge slowly
        latest_buoy_battery = max(0, min(100, latest_buoy_battery + battery_change))
        
        time.sleep(5)  # Update every 5 seconds

# Data logger for both systems
def data_logger():
    while True:
        time.sleep(5)  # Log every 5 seconds
        
        with app.app_context():
            try:
                # Log drone data
                if all(v is not None for v in [latest_drone_turbidity, latest_drone_temperature, 
                                              latest_drone_conductivity, latest_drone_ph, latest_drone_do]):
                    # Check thresholds
                    threshold_count = 0
                    if latest_drone_turbidity > DRONE_TURBIDITY_THRESHOLD:
                        threshold_count += 1
                    if latest_drone_temperature > DRONE_TEMPERATURE_THRESHOLD:
                        threshold_count += 1
                    if latest_drone_conductivity > DRONE_CONDUCTIVITY_THRESHOLD:
                        threshold_count += 1
                    if latest_drone_ph < DRONE_PH_THRESHOLD_LOW or latest_drone_ph > DRONE_PH_THRESHOLD_HIGH:
                        threshold_count += 1
                    if latest_drone_do < DRONE_DO_THRESHOLD:
                        threshold_count += 1
                    
                    above_threshold = threshold_count >= ALERT_THRESHOLD
                    
                    entry = DroneSensorData(
                        turbidity=latest_drone_turbidity,
                        temperature=latest_drone_temperature,
                        conductivity=latest_drone_conductivity,
                        ph=latest_drone_ph,
                        do=latest_drone_do,
                        latitude=latest_drone_latitude,
                        longitude=latest_drone_longitude,
                        battery=latest_drone_battery,
                        gps_type='real' if drone_connected else 'simulated',
                        above_threshold=above_threshold,
                        timestamp=datetime.now()
                    )
                    db.session.add(entry)
                    
                    # Log alert if needed
                    if above_threshold:
                        alert = SystemAlert(
                            system_type='drone',
                            alert_type='threshold_exceeded',
                            message=f'Drone: {threshold_count} sensors exceeded thresholds',
                            timestamp=datetime.now()
                        )
                        db.session.add(alert)
                        log_to_database('drone', 'warning', f'Threshold alert: {threshold_count} sensors exceeded')
                
                # Log buoy data
                if all(v is not None for v in [latest_buoy_turbidity, latest_buoy_temperature, 
                                              latest_buoy_conductivity, latest_buoy_ph, 
                                              latest_buoy_do, latest_buoy_pressure]):
                    # Check thresholds
                    threshold_count = 0
                    if latest_buoy_turbidity > BUOY_TURBIDITY_THRESHOLD:
                        threshold_count += 1
                    if latest_buoy_temperature > BUOY_TEMPERATURE_THRESHOLD:
                        threshold_count += 1
                    if latest_buoy_conductivity > BUOY_CONDUCTIVITY_THRESHOLD:
                        threshold_count += 1
                    if latest_buoy_ph < BUOY_PH_THRESHOLD_LOW or latest_buoy_ph > BUOY_PH_THRESHOLD_HIGH:
                        threshold_count += 1
                    if latest_buoy_do < BUOY_DO_THRESHOLD:
                        threshold_count += 1
                    if latest_buoy_pressure > BUOY_PRESSURE_THRESHOLD:
                        threshold_count += 1
                    
                    above_threshold = threshold_count >= ALERT_THRESHOLD
                    
                    entry = BuoySensorData(
                        turbidity=latest_buoy_turbidity,
                        temperature=latest_buoy_temperature,
                        conductivity=latest_buoy_conductivity,
                        ph=latest_buoy_ph,
                        do=latest_buoy_do,
                        pressure=latest_buoy_pressure,
                        latitude=latest_buoy_latitude,
                        longitude=latest_buoy_longitude,
                        gps_type='real' if buoy_connected else 'simulated',
                        above_threshold=above_threshold,
                        timestamp=datetime.now()
                    )
                    db.session.add(entry)
                    
                    if above_threshold:
                        alert = SystemAlert(
                            system_type='buoy',
                            alert_type='threshold_exceeded',
                            message=f'Buoy: {threshold_count} sensors exceeded thresholds',
                            timestamp=datetime.now()
                        )
                        db.session.add(alert)
                        log_to_database('buoy', 'warning', f'Threshold alert: {threshold_count} sensors exceeded')
                
                db.session.commit()
                
            except Exception as e:
                logger.error(f"Data logging error: {str(e)}")
                db.session.rollback()
                log_to_database('system', 'error', f'Data logging error: {str(e)}')

# Cleanup old data (1 year retention)
def cleanup_old_data():
    with app.app_context():
        try:
            cutoff_time = datetime.utcnow() - timedelta(days=365)  # 1 year
            
            # Delete old drone data
            deleted_drone = DroneSensorData.query.filter(DroneSensorData.timestamp < cutoff_time).delete()
            
            # Delete old buoy data
            deleted_buoy = BuoySensorData.query.filter(BuoySensorData.timestamp < cutoff_time).delete()
            
            # Delete old alerts (keep for 90 days only)
            alert_cutoff = datetime.utcnow() - timedelta(days=90)
            deleted_alerts = SystemAlert.query.filter(SystemAlert.timestamp < alert_cutoff).delete()
            
            # Delete old logs (keep for 30 days only)
            log_cutoff = datetime.utcnow() - timedelta(days=30)
            deleted_logs = SystemLog.query.filter(SystemLog.timestamp < log_cutoff).delete()
            
            db.session.commit()
            logger.info(f"Cleanup: Drone={deleted_drone}, Buoy={deleted_buoy}, Alerts={deleted_alerts}, Logs={deleted_logs}")
            log_to_database('system', 'info', f'Data cleanup completed')
            
        except Exception as e:
            logger.error(f"Cleanup error: {str(e)}")
            db.session.rollback()
            log_to_database('system', 'error', f'Cleanup error: {str(e)}')

def cleanup_scheduler():
    while True:
        cleanup_old_data()
        time.sleep(86400)  # Run daily

# Initialize camera with improved support
def init_camera():
    """Initialize camera with support for both Raspberry Pi Camera and USB Webcam"""
    global camera, camera_initialized, camera_type
    
    # Reset camera state
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
        time.sleep(2)  # Camera warm-up
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
        time.sleep(2)  # Camera warm-up
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
    
    # Try different camera indices
    camera_indices = [0, 1, 2, 3, 4]
    
    for i in camera_indices:
        try:
            logger.info(f"Trying camera index {i}")
            camera = cv2.VideoCapture(i)
            
            if camera.isOpened():
                # Test if we can actually read from this camera
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
    """Generate camera frame, with fallback to placeholder"""
    global camera, camera_initialized, camera_type
    
    try:
        if camera_type == "dummy" or not camera_initialized:
            # Generate placeholder frame
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            
            # Add background gradient
            for y in range(480):
                color = int(50 + (y / 480) * 50)
                cv2.line(frame, (0, y), (640, y), (color, color, 100), 1)
            
            # Add text
            cv2.putText(frame, "DRONE CAMERA FEED", (120, 150), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(frame, "Camera Initializing...", (200, 200), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
            
            # Add timestamp
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cv2.putText(frame, timestamp, (220, 250), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)
            
            # Add status
            cv2.putText(frame, "Status: Dummy Mode", (240, 280), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 200, 255), 1)
            
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            return buffer.tobytes()
        
        elif "usb_webcam" in camera_type and hasattr(camera, 'read'):
            with camera_lock:
                success, frame = camera.read()
            
            if success and frame is not None:
                # Add timestamp and overlay
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cv2.putText(frame, f"Drone Camera - {timestamp}", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                cv2.putText(frame, f"GPS: {latest_drone_latitude:.6f}, {latest_drone_longitude:.6f}", 
                           (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                
                ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                return buffer.tobytes()
            else:
                # Fallback to dummy frame
                return generate_camera_frame()
        
        else:
            # For Pi cameras, we need different handling
            return generate_camera_frame()
            
    except Exception as e:
        logger.error(f"Camera frame error: {str(e)}")
        # Generate error frame
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(frame, "CAMERA ERROR", (200, 240), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        cv2.putText(frame, str(e)[:50], (100, 280), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        ret, buffer = cv2.imencode('.jpg', frame)
        return buffer.tobytes()

def generate_frames():
    """Generate camera frames for streaming"""
    while True:
        try:
            frame_bytes = generate_camera_frame()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        except Exception as e:
            logger.error(f"Frame generation error: {str(e)}")
            # Generate error frame
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(frame, "STREAM ERROR", (200, 240), 
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            ret, buffer = cv2.imencode('.jpg', frame)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        
        time.sleep(0.033)  # ~30 FPS

# Generate statistics
def generate_statistics(system_type, days=30):
    """Generate statistics for the specified system and time period"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    if system_type == 'drone':
        data = DroneSensorData.query.filter(DroneSensorData.timestamp >= cutoff).all()
    else:
        data = BuoySensorData.query.filter(BuoySensorData.timestamp >= cutoff).all()
    
    if not data:
        return None
    
    # Convert to DataFrame for easier analysis
    df_data = []
    for entry in data:
        if system_type == 'drone':
            row = {
                'timestamp': entry.timestamp,
                'turbidity': entry.turbidity,
                'temperature': entry.temperature,
                'conductivity': entry.conductivity,
                'ph': entry.ph,
                'do': entry.do,
                'battery': entry.battery
            }
        else:
            row = {
                'timestamp': entry.timestamp,
                'turbidity': entry.turbidity,
                'temperature': entry.temperature,
                'conductivity': entry.conductivity,
                'ph': entry.ph,
                'do': entry.do,
                'pressure': entry.pressure
            }
        df_data.append(row)
    
    df = pd.DataFrame(df_data)
    
    stats = {}
    for column in df.columns:
        if column != 'timestamp' and df[column].notna().any():
            col_data = df[column].dropna()
            if len(col_data) > 0:
                stats[column] = {
                    'mean': float(col_data.mean()),
                    'median': float(col_data.median()),
                    'std': float(col_data.std()),
                    'min': float(col_data.min()),
                    'max': float(col_data.max()),
                    'q1': float(col_data.quantile(0.25)),
                    'q3': float(col_data.quantile(0.75)),
                    'count': int(len(col_data))
                }
    
    # Generate histograms
    histograms = {}
    for column in stats.keys():
        if column in df.columns:
            col_data = df[column].dropna()
            if len(col_data) > 0:
                hist, edges = np.histogram(col_data, bins=min(20, len(col_data)))
                histograms[column] = {
                    'counts': hist.tolist(),
                    'edges': edges.tolist()
                }
    
    return {
        'statistics': stats,
        'histograms': histograms,
        'sample_count': len(data)
    }

# Buoy statistics generator
def generate_buoy_statistics(days=30):
    """Generate statistics for buoy data"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    data = BuoySensorData.query.filter(BuoySensorData.timestamp >= cutoff).all()
    
    if not data:
        return None
    
    df_data = []
    for entry in data:
        row = {
            'timestamp': entry.timestamp,
            'turbidity': entry.turbidity,
            'temperature': entry.temperature,
            'pressure': entry.pressure,
            'conductivity': entry.conductivity,
            'ph': entry.ph,
            'do': entry.do
        }
        df_data.append(row)
    
    df = pd.DataFrame(df_data)
    
    stats = {}
    for column in df.columns:
        if column != 'timestamp' and df[column].notna().any():
            col_data = df[column].dropna()
            if len(col_data) > 0:
                stats[column] = {
                    'mean': float(col_data.mean()),
                    'median': float(col_data.median()),
                    'std': float(col_data.std()),
                    'min': float(col_data.min()),
                    'max': float(col_data.max()),
                    'q1': float(col_data.quantile(0.25)),
                    'q3': float(col_data.quantile(0.75)),
                    'count': int(len(col_data))
                }
    
    return {
        'statistics': stats,
        'sample_count': len(data)
    }

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
    return jsonify({
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
    })

@app.route('/api/drone/historical')
@login_required
def get_drone_historical_data():
    hours = request.args.get('hours', 24, type=int)
    limit = request.args.get('limit', 1000, type=int)
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    
    entries = DroneSensorData.query.filter(
        DroneSensorData.timestamp >= cutoff
    ).order_by(DroneSensorData.timestamp.asc()).limit(limit).all()
    
    data = [{
        'time': entry.timestamp.isoformat(),
        'turbidity': entry.turbidity,
        'temperature': entry.temperature,
        'conductivity': entry.conductivity,
        'ph': entry.ph,
        'do': entry.do,
        'latitude': entry.latitude,
        'longitude': entry.longitude,
        'battery': entry.battery,
        'above_threshold': entry.above_threshold
    } for entry in entries]
    
    return jsonify(data)

@app.route('/api/drone/statistics')
@login_required
def get_drone_statistics():
    days = request.args.get('days', 30, type=int)
    stats = generate_statistics('drone', days)
    return jsonify(stats or {})

@app.route('/api/drone/distribution')
@login_required
def get_drone_distribution_data():
    days = request.args.get('days', 30, type=int)
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    entries = DroneSensorData.query.filter(
        DroneSensorData.timestamp >= cutoff
    ).all()
    
    data = {}
    for entry in entries:
        for sensor in ['turbidity', 'temperature', 'conductivity', 'ph', 'do']:
            if sensor not in data:
                data[sensor] = []
            value = getattr(entry, sensor)
            if value is not None:
                data[sensor].append(value)
    
    return jsonify(data)

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
    hours = request.args.get('hours', 24, type=int)
    limit = request.args.get('limit', 1000, type=int)
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    
    entries = BuoySensorData.query.filter(
        BuoySensorData.timestamp >= cutoff
    ).order_by(BuoySensorData.timestamp.asc()).limit(limit).all()
    
    data = [{
        'time': entry.timestamp.isoformat(),
        'turbidity': entry.turbidity,
        'temperature': entry.temperature,
        'conductivity': entry.conductivity,
        'ph': entry.ph,
        'do': entry.do,
        'pressure': entry.pressure,
        'above_threshold': entry.above_threshold
    } for entry in entries]
    
    return jsonify(data)

@app.route('/api/buoy/statistics')
@login_required
def get_buoy_statistics():
    days = request.args.get('days', 30, type=int)
    stats = generate_buoy_statistics(days)
    return jsonify(stats or {})

# Debug & System Routes
@app.route('/api/debug/serial-status')
@login_required
def get_serial_debug_info():
    ports = [p.device for p in serial.tools.list_ports.comports()]
    
    return jsonify({
        'drone': {
            'connected': drone_connected,
            'reconnect_attempts': drone_reconnect_attempts,
            'port': DRONE_SERIAL_PORT,
            'last_update': drone_last_update.isoformat() if drone_last_update else None
        },
        'buoy': {
            'connected': buoy_connected,
            'reconnect_attempts': buoy_reconnect_attempts,
            'port': BUOY_SERIAL_PORT,
            'last_update': buoy_last_update.isoformat() if buoy_last_update else None
        },
        'available_ports': ports
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
    success = connect_to_drone()
    if success:
        return jsonify({'status': 'success', 'message': 'Drone reconnected successfully'})
    else:
        return jsonify({'status': 'error', 'message': 'Failed to reconnect drone'})

@app.route('/api/debug/reconnect-buoy')
@login_required
def reconnect_buoy():
    success = connect_to_buoy()
    if success:
        return jsonify({'status': 'success', 'message': 'Buoy reconnected successfully'})
    else:
        return jsonify({'status': 'error', 'message': 'Failed to reconnect buoy'})

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
    limit = request.args.get('limit', 100, type=int)
    level = request.args.get('level', '')
    source = request.args.get('source', '')
    
    query = SystemLog.query
    
    if level:
        query = query.filter(SystemLog.level == level)
    if source:
        query = query.filter(SystemLog.source == source)
    
    logs = query.order_by(SystemLog.timestamp.desc()).limit(limit).all()
    
    data = [{
        'timestamp': log.timestamp.isoformat(),
        'source': log.source,
        'level': log.level,
        'message': log.message
    } for log in logs]
    
    return jsonify(data)

# Camera Routes
@app.route('/video_feed')
@login_required
def video_feed():
    """Video streaming route"""
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/camera/snapshot')
@login_required
def take_snapshot_api():
    """Take a snapshot from the camera"""
    try:
        frame_bytes = generate_camera_frame()
        
        # Save snapshot to file
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
    """Export drone data as CSV"""
    days = request.args.get('days', 7, type=int)
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    entries = DroneSensorData.query.filter(
        DroneSensorData.timestamp >= cutoff
    ).order_by(DroneSensorData.timestamp.asc()).all()
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['Timestamp', 'Temperature (°C)', 'Turbidity (NTU)', 
                    'Conductivity (mS/cm)', 'pH', 'Dissolved Oxygen (mg/L)',
                    'Latitude', 'Longitude', 'Battery (%)', 'GPS Type', 'Alert'])
    
    # Write data
    for entry in entries:
        writer.writerow([
            entry.timestamp.isoformat(),
            entry.temperature,
            entry.turbidity,
            entry.conductivity,
            entry.ph,
            entry.do,
            entry.latitude,
            entry.longitude,
            entry.battery,
            entry.gps_type,
            'YES' if entry.above_threshold else 'NO'
        ])
    
    # Create response
    output.seek(0)
    
    # Log export
    log_to_database('system', 'info', f'Drone data exported for {days} days')
    
    # Create export record
    export_record = DataExport(
        user_id=current_user.id,
        system_type='drone',
        start_date=cutoff,
        end_date=datetime.utcnow(),
        format='csv',
        file_path=f'drone_export_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
    )
    db.session.add(export_record)
    db.session.commit()
    
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=drone_data_{days}_days.csv'}
    )

@app.route('/api/export/buoy-data')
@login_required
def export_buoy_data():
    """Export buoy data as CSV"""
    days = request.args.get('days', 7, type=int)
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    entries = BuoySensorData.query.filter(
        BuoySensorData.timestamp >= cutoff
    ).order_by(BuoySensorData.timestamp.asc()).all()
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['Timestamp', 'Temperature (°C)', 'Turbidity (NTU)', 
                    'Conductivity (mS/cm)', 'pH', 'Dissolved Oxygen (mg/L)',
                    'Pressure (bar)', 'Latitude', 'Longitude', 'GPS Type', 'Alert'])
    
    # Write data
    for entry in entries:
        writer.writerow([
            entry.timestamp.isoformat(),
            entry.temperature,
            entry.turbidity,
            entry.conductivity,
            entry.ph,
            entry.do,
            entry.pressure,
            entry.latitude,
            entry.longitude,
            entry.gps_type,
            'YES' if entry.above_threshold else 'NO'
        ])
    
    # Create response
    output.seek(0)
    
    # Log export
    log_to_database('system', 'info', f'Buoy data exported for {days} days')
    
    # Create export record
    export_record = DataExport(
        user_id=current_user.id,
        system_type='buoy',
        start_date=cutoff,
        end_date=datetime.utcnow(),
        format='csv',
        file_path=f'buoy_export_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
    )
    db.session.add(export_record)
    db.session.commit()
    
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=buoy_data_{days}_days.csv'}
    )

# Alerts API
@app.route('/api/alerts')
@login_required
def get_alerts():
    days = request.args.get('days', 7, type=int)
    resolved = request.args.get('resolved', 'false').lower() == 'true'
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    alerts = SystemAlert.query.filter(
        SystemAlert.timestamp >= cutoff,
        SystemAlert.resolved == resolved
    ).order_by(SystemAlert.timestamp.desc()).all()
    
    data = [{
        'id': alert.id,
        'timestamp': alert.timestamp.isoformat(),
        'system_type': alert.system_type,
        'alert_type': alert.alert_type,
        'message': alert.message,
        'resolved': alert.resolved,
        'resolved_at': alert.resolved_at.isoformat() if alert.resolved_at else None
    } for alert in alerts]
    
    return jsonify(data)

@app.route('/api/alerts/<int:alert_id>/resolve', methods=['POST'])
@login_required
def resolve_alert(alert_id):
    alert = SystemAlert.query.get_or_404(alert_id)
    alert.resolved = True
    alert.resolved_at = datetime.utcnow()
    db.session.commit()
    log_to_database('system', 'info', f'Alert {alert_id} resolved')
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
            'drone_records': DroneSensorData.query.count(),
            'buoy_records': BuoySensorData.query.count(),
            'alerts': SystemAlert.query.filter_by(resolved=False).count()
        }
    })

# Dashboard selector (removed - using header switcher instead)
# @app.route('/dashboard-selector')
# @login_required
# def dashboard_selector():
#     return render_template('dashboard_selector.html')

if __name__ == '__main__':
    with app.app_context():
        # Create all tables
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
        
        # Initialize from database
        initialize_from_database()
        
        # Log system startup
        log_to_database('system', 'info', 'System started')
    
    # Initialize camera
    camera_init_success = init_camera()
    if camera_init_success:
        logger.info(f"Camera initialized: {camera_type}")
    else:
        logger.warning("Camera initialization failed")
    
    # Start background threads
    threading.Thread(target=drone_serial_reader, daemon=True).start()
    threading.Thread(target=buoy_serial_reader, daemon=True).start()
    threading.Thread(target=buoy_wave_simulator, daemon=True).start()
    threading.Thread(target=data_logger, daemon=True).start()
    threading.Thread(target=cleanup_scheduler, daemon=True).start()
    
    # Try to connect to devices
    connect_to_drone()
    
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)