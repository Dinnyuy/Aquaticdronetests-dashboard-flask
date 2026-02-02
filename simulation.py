"""
simulation.py - Advanced data simulation for ATAWI-3A3 water monitoring system
Simulates both drone and buoy data with realistic patterns and anomalies
"""

import random
import time
import threading
from datetime import datetime, timedelta
import math
import json
import serial
import sys

class SensorSimulator:
    """Base class for sensor simulation with realistic patterns"""
    
    def __init__(self, base_value, variation_range, trend_cycle_hours=24, noise_level=0.1):
        self.base_value = base_value
        self.variation_range = variation_range
        self.trend_cycle_hours = trend_cycle_hours
        self.noise_level = noise_level
        self.trend_offset = random.uniform(0, 2 * math.pi)
        
    def get_value(self, timestamp=None):
        """Generate realistic sensor value with diurnal patterns"""
        if timestamp is None:
            timestamp = datetime.now()
        
        # Calculate time of day factor (0-1 over 24 hours)
        hour_of_day = timestamp.hour + timestamp.minute / 60
        time_factor = math.sin(2 * math.pi * hour_of_day / self.trend_cycle_hours + self.trend_offset)
        
        # Add random noise
        noise = random.uniform(-self.noise_level, self.noise_level)
        
        # Calculate final value
        value = self.base_value + (time_factor * self.variation_range / 2) + noise
        
        # Ensure value stays within reasonable bounds
        min_val = self.base_value - self.variation_range
        max_val = self.base_value + self.variation_range
        return max(min_val, min(value, max_val))

class DroneSimulation:
    """Comprehensive drone data simulation"""
    
    def __init__(self):
        # Initialize sensor simulators
        self.temperature_sim = SensorSimulator(28.0, 4.0)  # Base 28°C ±4°C
        self.turbidity_sim = SensorSimulator(15.0, 30.0)   # Base 15 NTU ±30 NTU
        self.conductivity_sim = SensorSimulator(8.0, 4.0)   # Base 8 mS/cm ±4 mS/cm
        self.ph_sim = SensorSimulator(7.2, 0.8)            # Base 7.2 ±0.8
        self.do_sim = SensorSimulator(6.5, 2.5)            # Base 6.5 mg/L ±2.5 mg/L
        
        # GPS parameters (Kribi, Cameroon area)
        self.latitude = 4.2105
        self.longitude = 6.4375
        self.battery = 85.0
        self.speed = 0.0
        self.heading = 0.0
        
        # Mission parameters
        self.mission_active = True
        self.waypoints = [
            (4.2105, 6.4375),  # Start
            (4.2110, 6.4380),
            (4.2115, 6.4385),
            (4.2120, 6.4380),
            (4.2115, 6.4375),
            (4.2110, 6.4370),
        ]
        self.current_waypoint = 0
        self.last_position_update = datetime.now()
        
    def update_position(self):
        """Simulate drone movement along waypoints"""
        now = datetime.now()
        time_diff = (now - self.last_position_update).total_seconds()
        
        if time_diff > 2:  # Update position every 2 seconds
            target_lat, target_lon = self.waypoints[self.current_waypoint]
            
            # Calculate movement
            lat_diff = target_lat - self.latitude
            lon_diff = target_lon - self.longitude
            
            # Move towards waypoint
            move_distance = 0.0001  # Small movement per update
            distance = math.sqrt(lat_diff**2 + lon_diff**2)
            
            if distance > 0.00005:  # If not at waypoint
                self.latitude += lat_diff / distance * move_distance
                self.longitude += lon_diff / distance * move_distance
                
                # Calculate speed (m/s)
                self.speed = move_distance * 111000 / time_diff  # Convert to m/s
                self.heading = math.degrees(math.atan2(lon_diff, lat_diff))
                
                # Update battery (drain based on movement)
                self.battery = max(0, self.battery - 0.005)
            else:
                # Reached waypoint, move to next
                self.current_waypoint = (self.current_waypoint + 1) % len(self.waypoints)
                self.speed = 0.0
            
            self.last_position_update = now
    
    def generate_data(self):
        """Generate complete drone dataset"""
        timestamp = datetime.now()
        
        # Update position
        self.update_position()
        
        # Generate sensor data
        data = {
            'timestamp': timestamp,
            'temperature': round(self.temperature_sim.get_value(timestamp), 2),
            'turbidity': round(self.turbidity_sim.get_value(timestamp), 2),
            'conductivity': round(self.conductivity_sim.get_value(timestamp), 2),
            'ph': round(self.ph_sim.get_value(timestamp), 2),
            'do': round(self.do_sim.get_value(timestamp), 2),
            'latitude': round(self.latitude, 6),
            'longitude': round(self.longitude, 6),
            'battery': round(self.battery, 1),
            'speed': round(self.speed, 2),
            'heading': round(self.heading, 1),
            'mission_active': self.mission_active,
            'waypoint': self.current_waypoint,
            'connected': True,
            'gps_satellites': random.randint(8, 12),
            'altitude': round(random.uniform(0.1, 2.0), 1),
        }
        
        # Simulate occasional sensor anomalies
        if random.random() < 0.01:  # 1% chance of anomaly
            data = self._add_anomaly(data)
        
        return data
    
    def _add_anomaly(self, data):
        """Add realistic sensor anomalies"""
        anomaly_type = random.choice(['turbidity_spike', 'ph_drop', 'temp_rise', 'do_drop'])
        
        if anomaly_type == 'turbidity_spike':
            data['turbidity'] = round(random.uniform(50, 80), 2)  # Pollution event
        elif anomaly_type == 'ph_drop':
            data['ph'] = round(random.uniform(5.5, 6.0), 2)  # Acidic water
        elif anomaly_type == 'temp_rise':
            data['temperature'] = round(random.uniform(32, 35), 2)  # Thermal pollution
        elif anomaly_type == 'do_drop':
            data['do'] = round(random.uniform(2.0, 4.0), 2)  # Low oxygen
        
        return data
    
    def get_serial_data(self):
        """Format data for serial transmission (Arduino format)"""
        data = self.generate_data()
        
        # Format for Arduino: TEMP:25.5|TURB:15.2|EC:8.1|PH:7.2|DO:6.5|LAT:4.2105|LON:6.4375|BAT:85.0
        serial_str = (
            f"TEMP:{data['temperature']:.1f}|"
            f"TURB:{data['turbidity']:.1f}|"
            f"EC:{data['conductivity']:.1f}|"
            f"PH:{data['ph']:.1f}|"
            f"DO:{data['do']:.1f}|"
            f"LAT:{data['latitude']:.6f}|"
            f"LON:{data['longitude']:.6f}|"
            f"BAT:{data['battery']:.1f}"
        )
        
        return serial_str

class BuoySimulation:
    """Comprehensive buoy data simulation with tidal patterns"""
    
    def __init__(self):
        # Water quality sensors
        self.temperature_sim = SensorSimulator(26.0, 3.0)
        self.turbidity_sim = SensorSimulator(8.0, 15.0)
        self.conductivity_sim = SensorSimulator(10.0, 5.0)
        self.ph_sim = SensorSimulator(7.5, 0.7)
        self.do_sim = SensorSimulator(5.8, 2.0)
        self.pressure_sim = SensorSimulator(1.5, 0.5, trend_cycle_hours=12.4)  # Tidal cycle
        
        # Wave and current simulation
        self.wave_height = 0.5
        self.current_speed = 0.2
        self.wave_direction = 45.0
        
        # Weather simulation
        self.air_temperature = 28.5
        self.wind_speed = 3.2
        self.wind_direction = 180.0
        self.humidity = 75.0
        
        # Energy system
        self.battery = 92.0
        self.solar_charging = 75.0
        
        # Fixed position
        self.latitude = 4.2105
        self.longitude = 6.4375
        
        # Tidal simulation
        self.tidal_phase = 0.0
        
    def update_tidal_effects(self):
        """Update tidal effects on buoy sensors"""
        now = datetime.now()
        
        # Calculate tidal phase (12.4 hour cycle)
        seconds_since_midnight = now.hour * 3600 + now.minute * 60 + now.second
        self.tidal_phase = (seconds_since_midnight / (12.4 * 3600)) * 2 * math.pi
        
        # Update wave height based on tide
        base_wave = 0.5
        tide_effect = math.sin(self.tidal_phase) * 0.3
        wind_effect = self.wind_speed / 10 * 0.4
        self.wave_height = max(0.1, base_wave + tide_effect + wind_effect + random.uniform(-0.1, 0.1))
        
        # Update current speed
        self.current_speed = 0.2 + abs(math.sin(self.tidal_phase)) * 0.3
        
        # Update pressure based on tide (depth change)
        tide_pressure = math.sin(self.tidal_phase) * 0.2
        self.pressure_sim.base_value = 1.5 + tide_pressure
        
        # Update weather
        self._update_weather(now)
        
        # Update solar charging
        self._update_solar(now)
    
    def _update_weather(self, timestamp):
        """Update weather conditions based on time of day"""
        hour = timestamp.hour
        
        # Diurnal temperature pattern
        base_temp = 28.0
        diurnal_variation = math.sin(2 * math.pi * (hour - 6) / 24) * 3.0  # Peak at 2pm
        self.air_temperature = base_temp + diurnal_variation + random.uniform(-1, 1)
        
        # Wind pattern (stronger during day)
        base_wind = 2.0
        wind_variation = math.sin(2 * math.pi * (hour - 12) / 24) * 1.5  # Peak at 12pm
        self.wind_speed = max(0.5, base_wind + wind_variation + random.uniform(-0.5, 0.5))
        
        # Humidity (inverse of temperature)
        base_humidity = 80.0
        humidity_variation = -diurnal_variation * 2.5  # Lower when hotter
        self.humidity = max(40, min(100, base_humidity + humidity_variation + random.uniform(-5, 5)))
    
    def _update_solar(self, timestamp):
        """Update solar charging based on time of day"""
        hour = timestamp.hour
        
        if 6 <= hour <= 18:  # Daytime
            # Peak at solar noon (12pm)
            solar_efficiency = 50 * math.sin(math.pi * (hour - 6) / 12)
            self.solar_charging = max(0, min(100, solar_efficiency))
            
            # Charge battery during day
            charge_rate = self.solar_charging / 100 * 0.02
            self.battery = min(100, self.battery + charge_rate)
        else:
            self.solar_charging = 0
            # Slow discharge at night
            self.battery = max(0, self.battery - 0.001)
    
    def generate_data(self):
        """Generate complete buoy dataset"""
        timestamp = datetime.now()
        
        # Update tidal and weather effects
        self.update_tidal_effects()
        
        # Generate data
        data = {
            'timestamp': timestamp,
            'temperature': round(self.temperature_sim.get_value(timestamp), 2),
            'turbidity': round(self.turbidity_sim.get_value(timestamp), 2),
            'conductivity': round(self.conductivity_sim.get_value(timestamp), 2),
            'ph': round(self.ph_sim.get_value(timestamp), 2),
            'do': round(self.do_sim.get_value(timestamp), 2),
            'pressure': round(self.pressure_sim.get_value(timestamp), 2),
            'wave_height': round(self.wave_height, 2),
            'current_speed': round(self.current_speed, 2),
            'wave_direction': round(self.wave_direction, 1),
            'air_temperature': round(self.air_temperature, 1),
            'wind_speed': round(self.wind_speed, 1),
            'wind_direction': round(self.wind_direction, 1),
            'humidity': round(self.humidity, 1),
            'battery': round(self.battery, 1),
            'solar_charging': round(self.solar_charging, 1),
            'latitude': self.latitude,
            'longitude': self.longitude,
            'connected': True,
            'tidal_phase': round(self.tidal_phase, 3),
        }
        
        # Simulate occasional storm events
        if random.random() < 0.005:  # 0.5% chance of storm
            data = self._add_storm_effects(data)
        
        return data
    
    def _add_storm_effects(self, data):
        """Add storm effects to buoy data"""
        # Increase waves and wind
        data['wave_height'] = round(random.uniform(2.0, 3.5), 2)
        data['wind_speed'] = round(random.uniform(8.0, 15.0), 1)
        data['current_speed'] = round(random.uniform(1.0, 2.0), 2)
        
        # Decrease air pressure
        data['pressure'] = round(random.uniform(0.8, 1.2), 2)
        
        # Increase turbidity
        data['turbidity'] = round(random.uniform(20.0, 40.0), 2)
        
        return data
    
    def get_serial_data(self):
        """Format data for serial transmission"""
        data = self.generate_data()
        
        serial_str = (
            f"TEMP:{data['temperature']:.1f}|"
            f"TURB:{data['turbidity']:.1f}|"
            f"EC:{data['conductivity']:.1f}|"
            f"PH:{data['ph']:.1f}|"
            f"DO:{data['do']:.1f}|"
            f"PRESS:{data['pressure']:.1f}|"
            f"WAVE:{data['wave_height']:.1f}|"
            f"CURRENT:{data['current_speed']:.1f}|"
            f"AIR_TEMP:{data['air_temperature']:.1f}|"
            f"WIND:{data['wind_speed']:.1f}|"
            f"HUM:{data['humidity']:.1f}|"
            f"BAT:{data['battery']:.1f}|"
            f"SOLAR:{data['solar_charging']:.1f}|"
            f"LAT:{data['latitude']:.6f}|"
            f"LON:{data['longitude']:.6f}"
        )
        
        return serial_str

class SimulationServer:
    """Main simulation server that runs both drone and buoy simulations"""
    
    def __init__(self, drone_port='COM4', buoy_port='COM5', baud_rate=9600):
        self.drone_sim = DroneSimulation()
        self.buoy_sim = BuoySimulation()
        self.drone_port = drone_port
        self.buoy_port = buoy_port
        self.baud_rate = baud_rate
        
        # Serial connections (optional - for real hardware simulation)
        self.drone_serial = None
        self.buoy_serial = None
        
        # Data buffers
        self.drone_data_history = []
        self.buoy_data_history = []
        self.max_history = 1000
        
        # Control flags
        self.running = False
        self.simulate_serial = True  # Set to True to simulate serial output
        
    def start(self):
        """Start the simulation server"""
        self.running = True
        
        # Try to open serial ports if simulating hardware
        if self.simulate_serial:
            self._setup_serial_ports()
        
        # Start simulation threads
        drone_thread = threading.Thread(target=self._drone_simulation_loop)
        buoy_thread = threading.Thread(target=self._buoy_simulation_loop)
        web_api_thread = threading.Thread(target=self._web_api_loop)
        
        drone_thread.daemon = True
        buoy_thread.daemon = True
        web_api_thread.daemon = True
        
        drone_thread.start()
        buoy_thread.start()
        web_api_thread.start()
        
        print("ATAWI-3A3 Simulation Server Started")
        print("=" * 50)
        print(f"Drone Simulation: Active (Port: {self.drone_port})")
        print(f"Buoy Simulation: Active (Port: {self.buoy_port})")
        print(f"Web API: http://localhost:5001")
        print(f"Data Logging: Active (5s intervals)")
        print("=" * 50)
        print("Press Ctrl+C to stop simulation")
        
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()
    
    def _setup_serial_ports(self):
        """Setup serial ports for simulation"""
        try:
            # Simulate drone serial port
            self.drone_serial = serial.Serial(self.drone_port, self.baud_rate, timeout=1)
            print(f"Connected to drone serial port: {self.drone_port}")
        except:
            print(f"Warning: Could not open drone serial port {self.drone_port}")
            print("Running in software-only simulation mode")
        
        try:
            # Simulate buoy serial port
            self.buoy_serial = serial.Serial(self.buoy_port, self.baud_rate, timeout=1)
            print(f"Connected to buoy serial port: {self.buoy_port}")
        except:
            print(f"Warning: Could not open buoy serial port {self.buoy_port}")
            print("Running in software-only simulation mode")
    
    def _drone_simulation_loop(self):
        """Main drone simulation loop"""
        while self.running:
            try:
                # Generate drone data
                drone_data = self.drone_sim.generate_data()
                serial_data = self.drone_sim.get_serial_data()
                
                # Store in history
                self.drone_data_history.append(drone_data)
                if len(self.drone_data_history) > self.max_history:
                    self.drone_data_history.pop(0)
                
                # Send to serial if connected
                if self.drone_serial and self.drone_serial.is_open:
                    self.drone_serial.write((serial_data + '\n').encode('utf-8'))
                
                # Log to console (optional)
                if random.random() < 0.1:  # Log 10% of updates
                    print(f"[DRONE] {drone_data['timestamp'].strftime('%H:%M:%S')} "
                          f"Temp: {drone_data['temperature']}°C | "
                          f"Turb: {drone_data['turbidity']} NTU | "
                          f"Pos: {drone_data['latitude']:.4f}, {drone_data['longitude']:.4f}")
                
                time.sleep(2)  # Update every 2 seconds
                
            except Exception as e:
                print(f"Error in drone simulation: {e}")
                time.sleep(5)
    
    def _buoy_simulation_loop(self):
        """Main buoy simulation loop"""
        while self.running:
            try:
                # Generate buoy data
                buoy_data = self.buoy_sim.generate_data()
                serial_data = self.buoy_sim.get_serial_data()
                
                # Store in history
                self.buoy_data_history.append(buoy_data)
                if len(self.buoy_data_history) > self.max_history:
                    self.buoy_data_history.pop(0)
                
                # Send to serial if connected
                if self.buoy_serial and self.buoy_serial.is_open:
                    self.buoy_serial.write((serial_data + '\n').encode('utf-8'))
                
                # Log to console (optional)
                if random.random() < 0.1:  # Log 10% of updates
                    print(f"[BUOY]  {buoy_data['timestamp'].strftime('%H:%M:%S')} "
                          f"Temp: {buoy_data['temperature']}°C | "
                          f"Wave: {buoy_data['wave_height']}m | "
                          f"Wind: {buoy_data['wind_speed']}m/s")
                
                time.sleep(5)  # Update every 5 seconds
                
            except Exception as e:
                print(f"Error in buoy simulation: {e}")
                time.sleep(5)
    
    def _web_api_loop(self):
        """Simple web API for data access"""
        from flask import Flask, jsonify
        
        app = Flask(__name__)
        
        @app.route('/api/drone/real-time')
        def get_drone_real_time():
            if self.drone_data_history:
                latest = self.drone_data_history[-1]
                # Convert datetime to string for JSON
                latest['timestamp'] = latest['timestamp'].isoformat()
                return jsonify(latest)
            return jsonify({'error': 'No data available'})
        
        @app.route('/api/buoy/real-time')
        def get_buoy_real_time():
            if self.buoy_data_history:
                latest = self.buoy_data_history[-1]
                latest['timestamp'] = latest['timestamp'].isoformat()
                return jsonify(latest)
            return jsonify({'error': 'No data available'})
        
        @app.route('/api/drone/historical')
        def get_drone_historical():
            hours = 24  # Default to 24 hours
            cutoff = datetime.now() - timedelta(hours=hours)
            
            filtered_data = [
                {**data, 'timestamp': data['timestamp'].isoformat()}
                for data in self.drone_data_history
                if data['timestamp'] >= cutoff
            ]
            return jsonify(filtered_data)
        
        @app.route('/api/buoy/historical')
        def get_buoy_historical():
            hours = 24  # Default to 24 hours
            cutoff = datetime.now() - timedelta(hours=hours)
            
            filtered_data = [
                {**data, 'timestamp': data['timestamp'].isoformat()}
                for data in self.buoy_data_history
                if data['timestamp'] >= cutoff
            ]
            return jsonify(filtered_data)
        
        # Run Flask in a separate thread
        import threading
        server_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5001, debug=False, use_reloader=False))
        server_thread.daemon = True
        server_thread.start()
    
    def stop(self):
        """Stop the simulation server"""
        self.running = False
        
        # Close serial ports
        if self.drone_serial and self.drone_serial.is_open:
            self.drone_serial.close()
        if self.buoy_serial and self.buoy_serial.is_open:
            self.buoy_serial.close()
        
        print("\nSimulation server stopped.")
        print(f"Total drone data points: {len(self.drone_data_history)}")
        print(f"Total buoy data points: {len(self.buoy_data_history)}")

def main():
    """Main entry point for the simulation"""
    print("ATAWI-3A3 Water Monitoring System - Simulation Module")
    print("=" * 50)
    
    # Configuration
    DRONE_PORT = 'COM4'
    BUOY_PORT = 'COM5'
    BAUD_RATE = 9600
    
    # Create and start simulation server
    simulator = SimulationServer(
        drone_port=DRONE_PORT,
        buoy_port=BUOY_PORT,
        baud_rate=BAUD_RATE
    )
    
    # Start simulation
    try:
        simulator.start()
    except KeyboardInterrupt:
        simulator.stop()
    except Exception as e:
        print(f"Error: {e}")
        simulator.stop()

if __name__ == "__main__":
    main()