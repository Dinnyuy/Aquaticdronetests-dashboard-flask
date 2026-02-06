# models.py - Database Models
from extensions import db
from datetime import datetime
from flask_login import UserMixin

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, default=False)
    last_login = db.Column(db.DateTime)
    preferred_dashboard = db.Column(db.String(20), default='drone')

class DroneSensorData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    device_id = db.Column(db.String(50), default='DRONE-001')
    
    # Sensor readings
    turbidity = db.Column(db.Float)
    temperature = db.Column(db.Float)
    conductivity = db.Column(db.Float)
    ph = db.Column(db.Float)
    do = db.Column(db.Float)
    
    # GPS
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    altitude = db.Column(db.Float)
    gps_quality = db.Column(db.String(20))
    
    # Status
    battery_level = db.Column(db.Float)
    connection_status = db.Column(db.String(20))
    above_threshold = db.Column(db.Boolean, default=False)

class DronePeakLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Peak values
    peak_temperature = db.Column(db.Float)
    peak_turbidity = db.Column(db.Float)
    peak_ec = db.Column(db.Float)
    peak_ph = db.Column(db.Float)
    peak_do = db.Column(db.Float)
    
    # Location
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    gps_quality = db.Column(db.String(20))

class BuoySensorData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    buoy_id = db.Column(db.String(50), default='BUOY-001')
    
    # Water quality sensors
    turbidity = db.Column(db.Float)
    temperature = db.Column(db.Float)
    conductivity = db.Column(db.Float)
    ph = db.Column(db.Float)
    do = db.Column(db.Float)
    
    # Buoy-specific sensors
    water_pressure = db.Column(db.Float)
    water_depth = db.Column(db.Float)
    wave_height = db.Column(db.Float)
    current_speed = db.Column(db.Float)
    current_direction = db.Column(db.Float)
    
    # Environmental sensors
    air_temperature = db.Column(db.Float)
    humidity = db.Column(db.Float)
    wind_speed = db.Column(db.Float)
    wind_direction = db.Column(db.Float)
    
    # GPS
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    gps_quality = db.Column(db.String(20))
    
    # Status
    battery_level = db.Column(db.Float)
    solar_charging = db.Column(db.Float)
    connection_status = db.Column(db.String(20))
    above_threshold = db.Column(db.Boolean, default=False)

class BuoyPeakLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Peak values
    peak_temperature = db.Column(db.Float)
    peak_turbidity = db.Column(db.Float)
    peak_ec = db.Column(db.Float)
    peak_ph = db.Column(db.Float)
    peak_do = db.Column(db.Float)
    peak_pressure = db.Column(db.Float)
    peak_wave_height = db.Column(db.Float)
    
    # Location
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)

class SystemLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    source = db.Column(db.String(50))  # 'drone', 'buoy', 'camera', 'system'
    level = db.Column(db.String(20))   # 'info', 'warning', 'error', 'critical'
    message = db.Column(db.Text)
    details = db.Column(db.Text)

class DataExportLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    export_type = db.Column(db.String(20))  # 'drone', 'buoy', 'all'
    date_range_start = db.Column(db.DateTime)
    date_range_end = db.Column(db.DateTime)
    file_format = db.Column(db.String(10))  # 'csv', 'json', 'excel'
    file_size = db.Column(db.Integer)  # in bytes