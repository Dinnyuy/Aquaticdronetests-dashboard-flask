"""
run_simulation.py - Integrate simulation with existing Flask app
"""

import threading
import time
from datetime import datetime, timedelta
import random
from simulation import DroneSimulation, BuoySimulation

# Global variables to store simulated data
drone_simulator = DroneSimulation()
buoy_simulator = BuoySimulation()

def start_simulation():
    """Start the simulation threads"""
    
    def drone_simulation_thread():
        """Thread for drone simulation"""
        while True:
            try:
                # Generate new drone data
                data = drone_simulator.generate_data()
                
                # Update global variables (these would be imported from app.py)
                # In practice, you would update the global variables in app.py
                # For now, we'll just store them here
                global latest_drone_data
                latest_drone_data = data
                
                # Simulate sending to serial
                serial_data = drone_simulator.get_serial_data()
                # print(f"Drone: {serial_data}")
                
                time.sleep(2)  # Update every 2 seconds
                
            except Exception as e:
                print(f"Drone simulation error: {e}")
                time.sleep(5)
    
    def buoy_simulation_thread():
        """Thread for buoy simulation"""
        while True:
            try:
                # Generate new buoy data
                data = buoy_simulator.generate_data()
                
                # Update global variables
                global latest_buoy_data
                latest_buoy_data = data
                
                # Simulate sending to serial
                serial_data = buoy_simulator.get_serial_data()
                # print(f"Buoy: {serial_data}")
                
                time.sleep(5)  # Update every 5 seconds
                
            except Exception as e:
                print(f"Buoy simulation error: {e}")
                time.sleep(5)
    
    # Start threads
    drone_thread = threading.Thread(target=drone_simulation_thread, daemon=True)
    buoy_thread = threading.Thread(target=buoy_simulation_thread, daemon=True)
    
    drone_thread.start()
    buoy_thread.start()
    
    print("Simulation started...")
    return drone_thread, buoy_thread

# Global variables for the simulated data
latest_drone_data = None
latest_buoy_data = None

# Function to get latest drone data (to be called from app.py)
def get_latest_drone_data():
    return latest_drone_data

# Function to get latest buoy data (to be called from app.py)
def get_latest_buoy_data():
    return latest_buoy_data

if __name__ == "__main__":
    # Test the simulation
    start_simulation()
    
    # Keep the main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Simulation stopped.")