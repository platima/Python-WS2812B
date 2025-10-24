"""
WS2812B LED Controller via SPI Interface
Provides an HTTP web server for controlling RGB LED strips

API Endpoints:
    GET /             - Web control interface
    GET /update       - Update all LED colors (query params: r, g, b [0-255])
    GET /update_led   - Update individual LED (query params: index, r, g, b)
    GET /health       - Health check and system status
    GET /api/docs     - API documentation

Example API calls:
    /update?r=255&g=0&b=0           # Set all LEDs to red
    /update_led?index=0&r=255&g=0&b=0  # Set first LED to red
    /health                          # Get system status
"""
import http.server
import socketserver
import urllib.parse
import threading
import spidev
import time
import json
import sys
import atexit
import platform
import os

# Configuration Constants
PORT = 8080  # HTTP server port (may require root/admin on Linux)
NUM_LEDS = 16  # Number of LEDs in the strip
DEFAULT_BRIGHTNESS = 64  # Default brightness (0-255)
RING_SPEED = 0.03  # Delay between LEDs in startup animation (seconds)

# Global state for current LED color (accessed by multiple threads)
led_state = {'r': DEFAULT_BRIGHTNESS, 'g': DEFAULT_BRIGHTNESS, 'b': DEFAULT_BRIGHTNESS}
# State for individual LEDs - list of (r, g, b) tuples
individual_led_state = [(DEFAULT_BRIGHTNESS, DEFAULT_BRIGHTNESS, DEFAULT_BRIGHTNESS)] * NUM_LEDS
lock = threading.Lock()  # Protects SPI communication and led_state from concurrent access
server_start_time = time.time()  # Track server uptime
update_count = 0  # Track number of LED updates

# === SPI Setup with Error Handling ===
# WS2812B LEDs use a specific timing protocol that can be approximated via SPI
spi = None
try:
    spi = spidev.SpiDev()
    spi.open(0, 0)  # Open SPI bus 0, device 0
    spi.max_speed_hz = 2400000  # 2.4 MHz SPI clock for WS2812B timing
    print("✓ SPI initialized successfully")
except Exception as e:
    print(f"✗ Failed to initialize SPI: {e}")
    print("  Make sure SPI is enabled and you have the necessary permissions.")
    sys.exit(1)

# Register cleanup function to ensure SPI is closed properly
def cleanup_spi():
    """Clean up SPI connection on program exit."""
    if spi is not None:
        try:
            spi.close()
            print("\n✓ SPI connection closed")
        except Exception as e:
            print(f"\n✗ Error closing SPI: {e}")

atexit.register(cleanup_spi)

# === System Stats Helpers (native Python only) ===
def get_cpu_count():
    """Get number of CPU cores."""
    try:
        return os.cpu_count() or "unknown"
    except:
        return "unknown"

def get_memory_info():
    """Get basic memory info from /proc/meminfo (Linux only)."""
    try:
        with open('/proc/meminfo', 'r') as f:
            lines = f.readlines()
            mem_info = {}
            for line in lines:
                if ':' in line:
                    key, value = line.split(':', 1)
                    mem_info[key.strip()] = value.strip()
            
            total = int(mem_info.get('MemTotal', '0').split()[0])
            available = int(mem_info.get('MemAvailable', '0').split()[0])
            if total > 0:
                used_percent = round((total - available) / total * 100, 1)
                return {
                    "total_mb": round(total / 1024, 1),
                    "available_mb": round(available / 1024, 1),
                    "used_percent": used_percent
                }
    except:
        pass
    return {"note": "Memory info not available"}

def get_uptime():
    """Get system uptime (Linux only)."""
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.read().split()[0])
            hours = int(uptime_seconds // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            return f"{hours}h {minutes}m"
    except:
        return "unknown"

def get_load_average():
    """Get system load average (Linux only)."""
    try:
        if hasattr(os, 'getloadavg'):
            return os.getloadavg()
    except:
        pass
    return None

def get_cpu_temp():
    """Get CPU temperature (Raspberry Pi & Compatible)."""
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp = float(f.read().strip()) / 1000.0
            return round(temp, 1)
    except:
        return None

# WS2812B timing encoded as SPI bits
# A '0' bit is represented as 100 (high-low-low)
# A '1' bit is represented as 110 (high-high-low)
BIT_0 = 0b100
BIT_1 = 0b110

# Pre-compute lookup table for all possible byte values
# This improves performance by avoiding repeated bit manipulation
LOOKUP = {}
for byte in range(256):
    bits = []
    for i in range(8):
        bit = (byte >> (7 - i)) & 0x01
        bits.append(BIT_1 if bit else BIT_0)
    LOOKUP[byte] = bits

def encode_led_data(rgb_data):
    """
    Encode RGB data for WS2812B LEDs into SPI-compatible byte stream.
    
    WS2812B expects data in GRB order (not RGB).
    Each color byte is expanded into 3-bit patterns and packed into bytes for SPI transmission.
    
    Args:
        rgb_data: List of (r, g, b) tuples, one per LED
        
    Returns:
        List of bytes ready to send via SPI
    """
    encoded = []
    # WS2812B uses GRB order, not RGB
    for (r, g, b) in rgb_data:
        for byte in (g, r, b):  # Note: GRB order
            encoded.extend(LOOKUP[byte])

    # Pack the 3-bit patterns into bytes for SPI transmission
    packed = []
    current_byte = 0
    bit_count = 0
    for triplet in encoded:
        for i in range(3):
            bit = (triplet >> (2 - i)) & 0x1
            current_byte = (current_byte << 1) | bit
            bit_count += 1
            if bit_count == 8:
                packed.append(current_byte)
                current_byte = 0
                bit_count = 0
    # Handle any remaining bits
    if bit_count > 0:
        packed.append(current_byte << (8 - bit_count))
    return packed

def run_ring_animation(spi, num_leds, brightness=64, delay=0.05):
    """
    Startup animation: light up each LED sequentially in a ring pattern.
    
    Args:
        spi: SPI device instance
        num_leds: Number of LEDs to animate
        brightness: Brightness level (0-255)
        delay: Delay between each LED step in seconds
    """
    for i in range(num_leds):
        # Start with all LEDs off
        rgb_list = [(0, 0, 0)] * num_leds

        # Light up one pixel at a time (white)
        rgb_list[i] = (brightness, brightness, brightness)

        # Encode and send data
        # Leading zeros are required for WS2812B reset timing
        spi_data = [0x00, 0x00, 0x00] + encode_led_data(rgb_list)
        spi.xfer2(spi_data)
        time.sleep(delay)

def update_leds(r, g, b):
    """
    Update all LEDs to the specified RGB color.
    Thread-safe via lock to prevent concurrent SPI access.
    
    Args:
        r: Red value (0-255)
        g: Green value (0-255)
        b: Blue value (0-255)
        
    Returns:
        bool: True if successful, False if failed
    """
    global update_count, individual_led_state
    
    with lock:
        try:
            # Set all LEDs to the same color
            rgb_list = [(r, g, b)] * NUM_LEDS
            spi_data = [0x00, 0x00, 0x00] + encode_led_data(rgb_list)
            spi.xfer2(spi_data)
            time.sleep(0.001)  # Small delay for LED latch
            update_count += 1
            # Update individual LED state tracking
            individual_led_state = rgb_list.copy()
            return True
        except Exception as e:
            print(f"Error updating LEDs: {e}")
            return False

def update_individual_led(index, r, g, b):
    """
    Update a single LED to the specified RGB color.
    Thread-safe via lock to prevent concurrent SPI access.
    
    Args:
        index: LED index (0 to NUM_LEDS-1)
        r: Red value (0-255)
        g: Green value (0-255)
        b: Blue value (0-255)
        
    Returns:
        bool: True if successful, False if failed
    """
    global update_count, individual_led_state
    
    if index < 0 or index >= NUM_LEDS:
        return False
    
    with lock:
        try:
            # Update the specific LED in our state
            individual_led_state[index] = (r, g, b)
            # Send entire strip state
            spi_data = [0x00, 0x00, 0x00] + encode_led_data(individual_led_state)
            spi.xfer2(spi_data)
            time.sleep(0.001)  # Small delay for LED latch
            update_count += 1
            return True
        except Exception as e:
            print(f"Error updating LED {index}: {e}")
            return False

class LEDRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler for LED control interface."""
    
    def log_message(self, format, *args):
        """Override to provide cleaner logging."""
        # Only log errors, not every request
        if args[1] != '200':
            super().log_message(format, *args)
    
    def do_GET(self):
        """Handle GET requests for both the web UI and LED updates."""
        
        # Health check endpoint with system stats
        if self.path == "/health":
            try:
                uptime_seconds = time.time() - server_start_time
                uptime_str = f"{int(uptime_seconds // 3600)}h {int((uptime_seconds % 3600) // 60)}m {int(uptime_seconds % 60)}s"
                
                with lock:
                    current_state = led_state.copy()
                
                # Build system stats using native Python functions
                system_stats = {
                    "platform": platform.system(),
                    "platform_release": platform.release(),
                    "python_version": platform.python_version(),
                    "cpu_count": get_cpu_count(),
                }
                
                # Add Linux-specific stats if available
                memory_info = get_memory_info()
                if "note" not in memory_info:
                    system_stats["memory"] = memory_info
                
                load_avg = get_load_average()
                if load_avg:
                    system_stats["load_average"] = [round(x, 2) for x in load_avg]
                
                cpu_temp = get_cpu_temp()
                if cpu_temp:
                    system_stats["cpu_temp_c"] = cpu_temp
                
                system_uptime = get_uptime()
                if system_uptime != "unknown":
                    system_stats["system_uptime"] = system_uptime
                
                health_data = {
                    "status": "ok",
                    "server_uptime_seconds": round(uptime_seconds, 2),
                    "server_uptime": uptime_str,
                    "updates_processed": update_count,
                    "num_leds": NUM_LEDS,
                    "current_color": current_state,
                    "system": system_stats
                }
                
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(health_data, indent=2).encode())
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
                return
        
        # API documentation endpoint
        if self.path == "/api/docs":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(bytes("""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>API Documentation - WS2812B LED Controller</title>
                    <meta charset="utf-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1">
                    <style>
                        body { font-family: monospace; padding: 20px; max-width: 800px; margin: 0 auto; }
                        h1, h2 { color: #333; }
                        .endpoint { background: #f4f4f4; padding: 15px; margin: 10px 0; border-radius: 5px; }
                        .method { color: #0066cc; font-weight: bold; }
                        .path { color: #006600; }
                        code { background: #e8e8e8; padding: 2px 5px; border-radius: 3px; }
                        .example { background: #e8f4f8; padding: 10px; margin: 5px 0; border-left: 3px solid #0066cc; }
                    </style>
                </head>
                <body>
                    <h1>🌈 WS2812B LED Controller API</h1>
                    <p>Control your RGB LED strip via HTTP requests.</p>
                    
                    <div class="endpoint">
                        <h2><span class="method">GET</span> <span class="path">/</span></h2>
                        <p><strong>Description:</strong> Web-based control interface with color sliders</p>
                        <div class="example">
                            <strong>Try it:</strong> <a href="/">Open Control Panel</a>
                        </div>
                    </div>
                    
                    <div class="endpoint">
                        <h2><span class="method">GET</span> <span class="path">/update</span></h2>
                        <p><strong>Description:</strong> Update all LED colors</p>
                        <p><strong>Query Parameters:</strong></p>
                        <ul>
                            <li><code>r</code> - Red value (0-255, optional)</li>
                            <li><code>g</code> - Green value (0-255, optional)</li>
                            <li><code>b</code> - Blue value (0-255, optional)</li>
                        </ul>
                        <p><strong>Response:</strong> Plain text "OK" on success</p>
                        <div class="example">
                            <strong>Examples:</strong><br>
                            <a href="/update?r=255&g=0&b=0">/update?r=255&g=0&b=0</a> - Red<br>
                            <a href="/update?r=0&g=255&b=0">/update?r=0&g=255&b=0</a> - Green<br>
                            <a href="/update?r=0&g=0&b=255">/update?r=0&g=0&b=255</a> - Blue<br>
                            <a href="/update?r=255&g=255&b=255">/update?r=255&g=255&b=255</a> - White<br>
                            <a href="/update?r=0&g=0&b=0">/update?r=0&g=0&b=0</a> - Off
                        </div>
                    </div>
                    
                    <div class="endpoint">
                        <h2><span class="method">GET</span> <span class="path">/update_led</span></h2>
                        <p><strong>Description:</strong> Update a single LED color</p>
                        <p><strong>Query Parameters:</strong></p>
                        <ul>
                            <li><code>index</code> - LED index (0-15, required)</li>
                            <li><code>r</code> - Red value (0-255, required)</li>
                            <li><code>g</code> - Green value (0-255, required)</li>
                            <li><code>b</code> - Blue value (0-255, required)</li>
                        </ul>
                        <p><strong>Response:</strong> JSON with status and LED info</p>
                        <div class="example">
                            <strong>Examples:</strong><br>
                            <a href="/update_led?index=0&r=255&g=0&b=0">/update_led?index=0&r=255&g=0&b=0</a> - First LED red<br>
                            <a href="/update_led?index=5&r=0&g=255&b=0">/update_led?index=5&r=0&g=255&b=0</a> - LED 5 green<br>
                            <a href="/update_led?index=15&r=0&g=0&b=255">/update_led?index=15&r=0&g=0&b=255</a> - Last LED blue<br>
                            <a href="/update_led?index=0&r=0&g=0&b=0">/update_led?index=0&r=0&g=0&b=0</a> - Turn off first LED
                        </div>
                    </div>
                    
                    <div class="endpoint">
                        <h2><span class="method">GET</span> <span class="path">/health</span></h2>
                        <p><strong>Description:</strong> System health check and statistics</p>
                        <p><strong>Response:</strong> JSON object with system information</p>
                        <div class="example">
                            <strong>Try it:</strong> <a href="/health">View Health Status</a>
                        </div>
                        <p><strong>Response Fields:</strong></p>
                        <ul>
                            <li><code>status</code> - Server status</li>
                            <li><code>uptime</code> - Server uptime</li>
                            <li><code>updates_processed</code> - Total LED updates</li>
                            <li><code>num_leds</code> - Number of LEDs</li>
                            <li><code>current_color</code> - Current RGB values</li>
                            <li><code>system</code> - Platform and resource stats</li>
                        </ul>
                    </div>
                    
                    <div class="endpoint">
                        <h2><span class="method">GET</span> <span class="path">/api/docs</span></h2>
                        <p><strong>Description:</strong> This documentation page</p>
                    </div>
                    
                    <hr>
                    <p><a href="/">← Back to Control Panel</a></p>
                </body>
                </html>
            """, "utf-8"))
            return
        
        # API endpoint for updating individual LED
        if self.path.startswith("/update_led"):
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            try:
                # Parse LED index and RGB values
                index = int(query.get("index", [-1])[0])
                r = int(query.get("r", [0])[0])
                g = int(query.get("g", [0])[0])
                b = int(query.get("b", [0])[0])
                
                # Validate index
                if index < 0 or index >= NUM_LEDS:
                    self.send_response(400)
                    self.send_header("Content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": f"Invalid index: {index}. Must be 0-{NUM_LEDS-1}"}).encode())
                    return
                
                # Clamp RGB values to valid range (0-255)
                r = max(0, min(255, r))
                g = max(0, min(255, g))
                b = max(0, min(255, b))
                
                # Send to LED
                success = update_individual_led(index, r, g, b)
                
                if success:
                    self.send_response(200)
                    self.send_header("Content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "ok", "index": index, "r": r, "g": g, "b": b}).encode())
                else:
                    self.send_response(500)
                    self.send_header("Content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Failed to update LED"}).encode())
                return
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Bad request: {str(e)}"}).encode())
                return
        
        # API endpoint for updating all LED colors
        if self.path.startswith("/update"):
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            try:
                # Parse RGB values from query string, defaulting to current state
                # Use lock to safely read current state
                with lock:
                    default_r = led_state['r']
                    default_g = led_state['g']
                    default_b = led_state['b']
                
                r = int(query.get("r", [default_r])[0])
                g = int(query.get("g", [default_g])[0])
                b = int(query.get("b", [default_b])[0])
                
                # Clamp values to valid range (0-255)
                r = max(0, min(255, r))
                g = max(0, min(255, g))
                b = max(0, min(255, b))
                
                # Update global state AND send to LEDs atomically (fixes race condition)
                with lock:
                    led_state.update({'r': r, 'g': g, 'b': b})
                
                # Send to LEDs (this also uses the lock internally)
                success = update_leds(r, g, b)
                
                if success:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'OK')
                else:
                    self.send_response(500)
                    self.send_header("Content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Failed to update LEDs"}).encode())
                return
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Bad request: {str(e)}"}).encode())
                return

        # Serve the web control interface
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(bytes(f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>RGB + White LED Control</title>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    /* Solarized Light Theme */
                    :root {{
                        --base03: #002b36;
                        --base02: #073642;
                        --base01: #586e75;
                        --base00: #657b83;
                        --base0: #839496;
                        --base1: #93a1a1;
                        --base2: #eee8d5;
                        --base3: #fdf6e3;
                        --yellow: #b58900;
                        --orange: #cb4b16;
                        --red: #dc322f;
                        --magenta: #d33682;
                        --violet: #6c71c4;
                        --blue: #268bd2;
                        --cyan: #2aa198;
                        --green: #859900;
                    }}
                    
                    body {{
                        font-family: sans-serif;
                        padding: 20px;
                        max-width: 600px;
                        margin: 0 auto;
                        background-color: var(--base3);
                        color: var(--base00);
                        transition: background-color 0.3s, color 0.3s;
                    }}
                    
                    /* Dark mode */
                    body.dark-mode {{
                        background-color: var(--base03);
                        color: var(--base0);
                    }}
                    
                    h1 {{
                        color: var(--blue);
                    }}
                    
                    label {{
                        display: block;
                        margin-top: 15px;
                        font-weight: 500;
                    }}
                    
                    .slider {{
                        width: calc(100% - 80px);
                        margin-right: 10px;
                    }}
                    
                    .value-input {{
                        width: 60px;
                        padding: 4px 8px;
                        border: 1px solid var(--base1);
                        background-color: var(--base2);
                        color: var(--base00);
                        border-radius: 3px;
                        font-size: 14px;
                    }}
                    
                    body.dark-mode .value-input {{
                        background-color: var(--base02);
                        color: var(--base0);
                        border-color: var(--base01);
                    }}
                    
                    .slider-container {{
                        display: flex;
                        align-items: center;
                        margin-top: 5px;
                    }}
                    
                    .header {{
                        display: flex;
                        justify-content: space-between;
                        align-items: center;
                        margin-bottom: 20px;
                    }}
                    
                    .links {{
                        font-size: 0.9em;
                    }}
                    
                    .links a {{
                        margin-left: 10px;
                        color: var(--blue);
                        text-decoration: none;
                    }}
                    
                    .links a:hover {{
                        text-decoration: underline;
                        color: var(--cyan);
                    }}
                    
                    .status {{
                        background: var(--base2);
                        padding: 10px;
                        margin: 15px 0;
                        border-radius: 5px;
                        font-size: 0.9em;
                        border-left: 4px solid var(--blue);
                        display: flex;
                        align-items: center;
                        gap: 15px;
                    }}
                    
                    body.dark-mode .status {{
                        background: var(--base02);
                    }}
                    
                    .color-preview {{
                        width: 40px;
                        height: 40px;
                        border-radius: 4px;
                        border: 2px solid var(--base1);
                        flex-shrink: 0;
                    }}
                    
                    body.dark-mode .color-preview {{
                        border-color: var(--base01);
                    }}
                    
                    .footer {{
                        margin-top: 30px;
                        padding-top: 15px;
                        border-top: 1px solid var(--base1);
                        font-size: 0.85em;
                        display: flex;
                        justify-content: space-between;
                        align-items: center;
                        color: var(--base1);
                    }}
                    
                    body.dark-mode .footer {{
                        border-top-color: var(--base01);
                    }}
                    
                    .footer a {{
                        color: var(--blue);
                        text-decoration: none;
                    }}
                    
                    .footer a:hover {{
                        text-decoration: underline;
                    }}
                    
                    .theme-toggle {{
                        cursor: pointer;
                        padding: 5px 10px;
                        background-color: var(--base2);
                        border: 1px solid var(--base1);
                        border-radius: 4px;
                        color: var(--base00);
                        font-size: 0.85em;
                    }}
                    
                    body.dark-mode .theme-toggle {{
                        background-color: var(--base02);
                        border-color: var(--base01);
                        color: var(--base0);
                    }}
                    
                    .theme-toggle:hover {{
                        background-color: var(--cyan);
                        color: var(--base3);
                        border-color: var(--cyan);
                    }}
                    
                    .color-picker-wrapper {{
                        position: relative;
                        cursor: pointer;
                    }}
                    
                    #color_picker {{
                        position: absolute;
                        top: 0;
                        left: 0;
                        opacity: 0;
                        width: 100%;
                        height: 100%;
                        cursor: pointer;
                        border: none;
                    }}
                </style>
            </head>
            <body>
                <div class="header">
                    <h1>LED Controller</h1>
                    <div class="links">
                        <a href="/health" target="_blank">📊 Health</a>
                        <a href="/api/docs" target="_blank">📖 API Docs</a>
                    </div>
                </div>
                <div class="status">
                    <div class="color-picker-wrapper">
                        <div class="color-preview" id="color_preview"></div>
                        <input type="color" id="color_picker" title="Click to pick a color">
                    </div>
                    <div>
                        <strong>LEDs:</strong> {NUM_LEDS} | 
                        <span id="rgb_display"></span> | 
                        <span id="hex_display" style="cursor: pointer; user-select: all;" title="Click to edit"></span>
                        <input type="text" id="hex_input" style="display: none; width: 80px; padding: 2px 4px; font-family: monospace;" maxlength="7" pattern="#[0-9A-Fa-f]{{6}}">
                    </div>
                </div>
                <label>
                    White:
                    <div class="slider-container">
                        <input type="range" min="0" max="255" value="{led_state['r']}" id="w" class="slider">
                        <input type="number" min="0" max="255" value="{led_state['r']}" id="w_val" class="value-input">
                    </div>
                </label>

                <label>
                    Red:
                    <div class="slider-container">
                        <input type="range" min="0" max="255" value="{led_state['r']}" id="r" class="slider">
                        <input type="number" min="0" max="255" value="{led_state['r']}" id="r_val" class="value-input">
                    </div>
                </label>
                <label>
                    Green:
                    <div class="slider-container">
                        <input type="range" min="0" max="255" value="{led_state['g']}" id="g" class="slider">
                        <input type="number" min="0" max="255" value="{led_state['g']}" id="g_val" class="value-input">
                    </div>
                </label>
                <label>
                    Blue:
                    <div class="slider-container">
                        <input type="range" min="0" max="255" value="{led_state['b']}" id="b" class="slider">
                        <input type="number" min="0" max="255" value="{led_state['b']}" id="b_val" class="value-input">
                    </div>
                </label>
                
                <div class="footer">
                    <div>
                        <a href="https://github.com/platima/Python-WS2812B" target="_blank">🔗 GitHub Repository</a>
                    </div>
                    <div>
                        <button class="theme-toggle" id="theme_toggle">Switch to Light Mode</button>
                    </div>
                </div>

                <script>
                    // Cookie management
                    function setCookie(name, value, days) {{
                        const d = new Date();
                        d.setTime(d.getTime() + (days * 24 * 60 * 60 * 1000));
                        document.cookie = name + "=" + value + ";expires=" + d.toUTCString() + ";path=/";
                    }}
                    
                    function getCookie(name) {{
                        const nameEQ = name + "=";
                        const ca = document.cookie.split(';');
                        for (let i = 0; i < ca.length; i++) {{
                            let c = ca[i];
                            while (c.charAt(0) === ' ') c = c.substring(1, c.length);
                            if (c.indexOf(nameEQ) === 0) return c.substring(nameEQ.length, c.length);
                        }}
                        return null;
                    }}
                    
                    // Dark mode detection and handling
                    function setDarkMode(isDark, updateToggle = true) {{
                        if (isDark) {{
                            document.body.classList.add('dark-mode');
                            if (updateToggle) {{
                                document.getElementById('theme_toggle').textContent = 'Switch to Light Mode';
                            }}
                        }} else {{
                            document.body.classList.remove('dark-mode');
                            if (updateToggle) {{
                                document.getElementById('theme_toggle').textContent = 'Switch to Dark Mode';
                            }}
                        }}
                    }}
                    
                    // Check for saved preference first, then system preference
                    const savedTheme = getCookie('theme');
                    if (savedTheme) {{
                        setDarkMode(savedTheme === 'dark');
                    }} else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {{
                        setDarkMode(true);
                    }} else {{
                        setDarkMode(false);
                    }}
                    
                    // Theme toggle button
                    document.getElementById('theme_toggle').addEventListener('click', function() {{
                        const isDark = document.body.classList.contains('dark-mode');
                        setDarkMode(!isDark);
                        setCookie('theme', !isDark ? 'dark' : 'light', 365);
                    }});
                    
                    // Listen for system theme changes (only if no saved preference)
                    if (window.matchMedia && !savedTheme) {{
                        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', event => {{
                            if (!getCookie('theme')) {{
                                setDarkMode(event.matches);
                            }}
                        }});
                    }}
                    
                    function updateColorPreview(r, g, b) {{
                        const preview = document.getElementById('color_preview');
                        preview.style.backgroundColor = `rgb(${{r}}, ${{g}}, ${{b}})`;
                        
                        // Update RGB display
                        document.getElementById('rgb_display').textContent = `rgb(${{r}},${{g}},${{b}})`;
                        
                        // Update hex display
                        const hex = '#' + [r, g, b].map(x => {{
                            const hex = x.toString(16).padStart(2, '0');
                            return hex;
                        }}).join('');
                        document.getElementById('hex_display').textContent = hex;
                    }}
                    
                    // Hex editing functionality
                    const hexDisplay = document.getElementById('hex_display');
                    const hexInput = document.getElementById('hex_input');
                    
                    hexDisplay.addEventListener('click', function() {{
                        hexInput.value = hexDisplay.textContent;
                        hexDisplay.style.display = 'none';
                        hexInput.style.display = 'inline';
                        hexInput.focus();
                        hexInput.select();
                    }});
                    
                    hexInput.addEventListener('blur', function() {{
                        applyHexInput();
                    }});
                    
                    hexInput.addEventListener('keydown', function(e) {{
                        if (e.key === 'Enter') {{
                            applyHexInput();
                        }} else if (e.key === 'Escape') {{
                            hexInput.style.display = 'none';
                            hexDisplay.style.display = 'inline';
                        }}
                    }});
                    
                    function applyHexInput() {{
                        const hexValue = hexInput.value.trim();
                        const hexRegex = /^#?([0-9A-Fa-f]{{6}})$/;
                        const match = hexValue.match(hexRegex);
                        
                        if (match) {{
                            const hex = match[1];
                            const r = parseInt(hex.substring(0, 2), 16);
                            const g = parseInt(hex.substring(2, 4), 16);
                            const b = parseInt(hex.substring(4, 6), 16);
                            
                            // Update all controls
                            document.getElementById("r").value = r;
                            document.getElementById("r_val").value = r;
                            document.getElementById("g").value = g;
                            document.getElementById("g_val").value = g;
                            document.getElementById("b").value = b;
                            document.getElementById("b_val").value = b;
                            
                            sendUpdate(r, g, b);
                        }}
                        
                        hexInput.style.display = 'none';
                        hexDisplay.style.display = 'inline';
                    }}
                    
                    function sendUpdate(r, g, b) {{
                        fetch(`/update?r=${{r}}&g=${{g}}&b=${{b}}`);
                        updateColorPreview(r, g, b);
                        
                        // Update color picker
                        const hex = '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('');
                        document.getElementById('color_picker').value = hex;
                    }}
                    
                    // Color picker functionality
                    document.getElementById('color_picker').addEventListener('input', function(e) {{
                        const hex = e.target.value;
                        const r = parseInt(hex.substring(1, 3), 16);
                        const g = parseInt(hex.substring(3, 5), 16);
                        const b = parseInt(hex.substring(5, 7), 16);
                        
                        // Update all controls
                        document.getElementById("r").value = r;
                        document.getElementById("r_val").value = r;
                        document.getElementById("g").value = g;
                        document.getElementById("g_val").value = g;
                        document.getElementById("b").value = b;
                        document.getElementById("b_val").value = b;
                        
                        sendUpdate(r, g, b);
                    }});

                    function updateFromRGB() {{
                        let r = parseInt(document.getElementById("r").value);
                        let g = parseInt(document.getElementById("g").value);
                        let b = parseInt(document.getElementById("b").value);
                        
                        // Sync slider and input
                        document.getElementById("r_val").value = r;
                        document.getElementById("g_val").value = g;
                        document.getElementById("b_val").value = b;
                        
                        sendUpdate(r, g, b);
                    }}
                    
                    function updateFromRGBInput() {{
                        let r = Math.max(0, Math.min(255, parseInt(document.getElementById("r_val").value) || 0));
                        let g = Math.max(0, Math.min(255, parseInt(document.getElementById("g_val").value) || 0));
                        let b = Math.max(0, Math.min(255, parseInt(document.getElementById("b_val").value) || 0));
                        
                        // Sync slider and input
                        document.getElementById("r").value = r;
                        document.getElementById("r_val").value = r;
                        document.getElementById("g").value = g;
                        document.getElementById("g_val").value = g;
                        document.getElementById("b").value = b;
                        document.getElementById("b_val").value = b;
                        
                        sendUpdate(r, g, b);
                    }}

                    function updateFromWhite() {{
                        let w = parseInt(document.getElementById("w").value);
                        document.getElementById("w_val").value = w;
                        
                        // Set RGB sliders and inputs to match white
                        ['r', 'g', 'b'].forEach(id => {{
                            document.getElementById(id).value = w;
                            document.getElementById(id + "_val").value = w;
                        }});
                        
                        sendUpdate(w, w, w);
                    }}
                    
                    function updateFromWhiteInput() {{
                        let w = Math.max(0, Math.min(255, parseInt(document.getElementById("w_val").value) || 0));
                        document.getElementById("w").value = w;
                        document.getElementById("w_val").value = w;
                        
                        // Set RGB sliders and inputs to match white
                        ['r', 'g', 'b'].forEach(id => {{
                            document.getElementById(id).value = w;
                            document.getElementById(id + "_val").value = w;
                        }});
                        
                        sendUpdate(w, w, w);
                    }}

                    // Attach event listeners for sliders
                    document.getElementById("w").addEventListener("input", updateFromWhite);
                    document.getElementById("r").addEventListener("input", updateFromRGB);
                    document.getElementById("g").addEventListener("input", updateFromRGB);
                    document.getElementById("b").addEventListener("input", updateFromRGB);
                    
                    // Attach event listeners for number inputs
                    document.getElementById("w_val").addEventListener("input", updateFromWhiteInput);
                    document.getElementById("r_val").addEventListener("input", updateFromRGBInput);
                    document.getElementById("g_val").addEventListener("input", updateFromRGBInput);
                    document.getElementById("b_val").addEventListener("input", updateFromRGBInput);
                    
                    // Initialize color preview
                    updateColorPreview({led_state['r']}, {led_state['g']}, {led_state['b']});
                </script>
            </body>
            </html>
        """, "utf-8"))

def start_server():
    """Start the HTTP server to serve the LED control interface."""
    handler = LEDRequestHandler
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        httpd.serve_forever()

def get_local_ip():
    """Get the local IP address of the device."""
    try:
        # Try to get wlan0 IP address on Linux
        import socket
        import fcntl
        import struct
        
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Try wlan0 first
            ip = socket.inet_ntoa(fcntl.ioctl(
                s.fileno(),
                0x8915,  # SIOCGIFADDR
                struct.pack('256s', b'wlan0'[:15])
            )[20:24])
            return ip
        except:
            # Fall back to eth0 or other interfaces
            try:
                ip = socket.inet_ntoa(fcntl.ioctl(
                    s.fileno(),
                    0x8915,
                    struct.pack('256s', b'eth0'[:15])
                )[20:24])
                return ip
            except:
                pass
    except:
        pass
    
    # Fallback method
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "localhost"

if __name__ == "__main__":
    try:
        # Run ring animation once at startup to indicate readiness
        print(f"  Running startup animation...")
        run_ring_animation(spi, NUM_LEDS, DEFAULT_BRIGHTNESS, delay=RING_SPEED)

        # Initialize LEDs to current state
        update_leds(led_state['r'], led_state['g'], led_state['b'])
        print(f"✓ Initialized {NUM_LEDS} LEDs")

        # Start the web server in a daemon thread (exits when main thread exits)
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()
        
        local_ip = get_local_ip()
        
        print(f"\n{'='*50}")
        print(f"🌈 WS2812B LED Controller Started")
        print(f"{'='*50}")
        print(f"  Control Panel: http://{local_ip}:{PORT}")
        print(f"  API Docs:      http://{local_ip}:{PORT}/api/docs")
        print(f"  Health Check:  http://{local_ip}:{PORT}/health")
        print(f"  LEDs:          {NUM_LEDS} connected")
        print(f"{'='*50}")
        print(f"\nPress Ctrl+C to stop the server\n")

        # Keep main thread alive
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n\n⏹ Shutting down gracefully...")
        # atexit handler will clean up SPI
    except Exception as e:
        print(f"\n✗ Fatal error: {e}")
        sys.exit(1)