#!/usr/bin/env python3
"""
Example API client for WS2812B LED Controller

Demonstrates how to control the LEDs programmatically via HTTP requests.
Run this on any device that can reach the LED controller's IP address.

Uses only native Python libraries (urllib) - no external dependencies!
"""

import urllib.request
import urllib.parse
import urllib.error
import json
import time
import sys

# Configuration - update with your LED controller's address
LED_CONTROLLER_URL = "http://localhost:8080"


def set_color(r, g, b):
    """Set all LEDs to a specific RGB color."""
    try:
        params = urllib.parse.urlencode({"r": r, "g": g, "b": b})
        url = f"{LED_CONTROLLER_URL}/update?{params}"
        
        with urllib.request.urlopen(url, timeout=5) as response:
            if response.status == 200:
                print(f"✓ Set LEDs to RGB({r}, {g}, {b})")
                return True
            else:
                print(f"✗ Error: {response.status}")
                return False
    except urllib.error.URLError as e:
        print(f"✗ Connection error: {e}")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def get_health():
    """Get health status and system information."""
    try:
        url = f"{LED_CONTROLLER_URL}/health"
        
        with urllib.request.urlopen(url, timeout=5) as response:
            if response.status == 200:
                data = response.read().decode('utf-8')
                return json.loads(data)
            else:
                print(f"✗ Error: {response.status}")
                return None
    except urllib.error.URLError as e:
        print(f"✗ Connection error: {e}")
        return None
    except Exception as e:
        print(f"✗ Error: {e}")
        return None


def rainbow_cycle(delay=0.1):
    """Cycle through rainbow colors."""
    print("\n🌈 Rainbow cycle (Ctrl+C to stop)...")
    colors = [
        (255, 0, 0),    # Red
        (255, 127, 0),  # Orange
        (255, 255, 0),  # Yellow
        (0, 255, 0),    # Green
        (0, 0, 255),    # Blue
        (75, 0, 130),   # Indigo
        (148, 0, 211),  # Violet
    ]
    
    try:
        while True:
            for r, g, b in colors:
                set_color(r, g, b)
                time.sleep(delay)
    except KeyboardInterrupt:
        print("\n⏹ Stopped")


def breathing_effect(color=(255, 255, 255), steps=20, delay=0.05):
    """Create a breathing effect with the specified color."""
    print(f"\n💨 Breathing effect with RGB{color} (Ctrl+C to stop)...")
    r, g, b = color
    
    try:
        while True:
            # Fade in
            for i in range(steps + 1):
                brightness = i / steps
                set_color(int(r * brightness), int(g * brightness), int(b * brightness))
                time.sleep(delay)
            
            # Fade out
            for i in range(steps, -1, -1):
                brightness = i / steps
                set_color(int(r * brightness), int(g * brightness), int(b * brightness))
                time.sleep(delay)
    except KeyboardInterrupt:
        print("\n⏹ Stopped")


def main():
    """Main demo function."""
    print("=" * 50)
    print("WS2812B LED Controller - API Client Demo")
    print("=" * 50)
    print(f"\nController: {LED_CONTROLLER_URL}\n")
    
    # Check if controller is reachable
    print("Checking controller health...")
    health = get_health()
    if health:
        print("✓ Controller is online")
        print(f"  Status: {health['status']}")
        print(f"  LEDs: {health['num_leds']}")
        print(f"  Uptime: {health['server_uptime']}")
        print(f"  Platform: {health['system']['platform']}")
    else:
        print("✗ Cannot connect to controller")
        print(f"  Make sure the server is running at {LED_CONTROLLER_URL}")
        sys.exit(1)
    
    print("\n" + "=" * 50)
    print("Choose a demo:")
    print("=" * 50)
    print("1. Rainbow cycle")
    print("2. White breathing effect")
    print("3. Red breathing effect")
    print("4. Set to white")
    print("5. Set to red")
    print("6. Set to green")
    print("7. Set to blue")
    print("8. Turn off")
    print("9. Exit")
    
    try:
        choice = input("\nEnter choice (1-9): ").strip()
        
        if choice == "1":
            rainbow_cycle()
        elif choice == "2":
            breathing_effect((255, 255, 255))
        elif choice == "3":
            breathing_effect((255, 0, 0))
        elif choice == "4":
            set_color(255, 255, 255)
        elif choice == "5":
            set_color(255, 0, 0)
        elif choice == "6":
            set_color(0, 255, 0)
        elif choice == "7":
            set_color(0, 0, 255)
        elif choice == "8":
            set_color(0, 0, 0)
            print("✓ LEDs turned off")
        elif choice == "9":
            print("Goodbye!")
        else:
            print("Invalid choice")
    
    except KeyboardInterrupt:
        print("\n\nGoodbye!")


if __name__ == "__main__":
    main()
