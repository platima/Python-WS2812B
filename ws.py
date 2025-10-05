"""
WS2812B LED Controller via SPI Interface
Provides an HTTP web server for controlling RGB LED strips

API Endpoints:
    GET /             - Web control interface
    GET /update       - Update LED colors (query params: r, g, b [0-255])
    GET /health       - Health check and system status
    GET /api/docs     - API documentation

Example API calls:
    /update?r=255&g=0&b=0     # Set all LEDs to red
    /update?r=128&g=128&b=128 # Set all LEDs to gray
    /health                    # Get system status
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
    """Get CPU temperature (Raspberry Pi)."""
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
    global update_count
    
    with lock:
        try:
            # Set all LEDs to the same color
            rgb_list = [(r, g, b)] * NUM_LEDS
            spi_data = [0x00, 0x00, 0x00] + encode_led_data(rgb_list)
            spi.xfer2(spi_data)
            time.sleep(0.001)  # Small delay for LED latch
            update_count += 1
            return True
        except Exception as e:
            print(f"Error updating LEDs: {e}")
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
                        <p><strong>Description:</strong> Update LED colors</p>
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
        
        # API endpoint for updating LED colors
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
                    body {{ font-family: sans-serif; padding: 20px; max-width: 600px; margin: 0 auto; }}
                    label {{ display: block; margin-top: 10px; }}
                    .slider {{ width: 100%; }}
                    .header {{ display: flex; justify-content: space-between; align-items: center; }}
                    .links {{ font-size: 0.9em; }}
                    .links a {{ margin-left: 10px; color: #0066cc; text-decoration: none; }}
                    .links a:hover {{ text-decoration: underline; }}
                    .status {{ background: #f0f0f0; padding: 10px; margin: 15px 0; border-radius: 5px; font-size: 0.9em; }}
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
                    <strong>LEDs:</strong> {NUM_LEDS} | 
                    <strong>Current:</strong> R={led_state['r']} G={led_state['g']} B={led_state['b']}
                </div>
                <label>White:
                    <input type="range" min="0" max="255" value="{led_state['r']}" id="w" class="slider">
                    <output id="w_val">{led_state['r']}</output>
                </label>

                <label>Red:
                    <input type="range" min="0" max="255" value="{led_state['r']}" id="r" class="slider">
                    <output id="r_val">{led_state['r']}</output>
                </label>
                <label>Green:
                    <input type="range" min="0" max="255" value="{led_state['g']}" id="g" class="slider">
                    <output id="g_val">{led_state['g']}</output>
                </label>
                <label>Blue:
                    <input type="range" min="0" max="255" value="{led_state['b']}" id="b" class="slider">
                    <output id="b_val">{led_state['b']}</output>
                </label>

                <script>
                    function sendUpdate(r, g, b) {{
                        fetch(`/update?r=${{r}}&g=${{g}}&b=${{b}}`);
                    }}

                    function updateFromRGB() {{
                        let r = parseInt(document.getElementById("r").value);
                        let g = parseInt(document.getElementById("g").value);
                        let b = parseInt(document.getElementById("b").value);
                        document.getElementById("r_val").textContent = r;
                        document.getElementById("g_val").textContent = g;
                        document.getElementById("b_val").textContent = b;
                        sendUpdate(r, g, b);
                    }}

                    function updateFromWhite() {{
                        let w = parseInt(document.getElementById("w").value);
                        document.getElementById("w_val").textContent = w;
                        // Set RGB sliders to match white
                        ['r', 'g', 'b'].forEach(id => {{
                            document.getElementById(id).value = w;
                            document.getElementById(id + "_val").textContent = w;
                        }});
                        sendUpdate(w, w, w);
                    }}

                    // Attach event listeners
                    document.getElementById("w").addEventListener("input", updateFromWhite);
                    document.getElementById("r").addEventListener("input", updateFromRGB);
                    document.getElementById("g").addEventListener("input", updateFromRGB);
                    document.getElementById("b").addEventListener("input", updateFromRGB);
                </script>
            </body>
            </html>
        """, "utf-8"))

def start_server():
    """Start the HTTP server to serve the LED control interface."""
    handler = LEDRequestHandler
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        print(f"Serving on http://localhost:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    try:
        # Run ring animation once at startup to indicate readiness
        print(f"Running startup animation...")
        run_ring_animation(spi, NUM_LEDS, DEFAULT_BRIGHTNESS, delay=RING_SPEED)

        # Initialize LEDs to current state
        update_leds(led_state['r'], led_state['g'], led_state['b'])
        print(f"✓ Initialized {NUM_LEDS} LEDs")

        # Start the web server in a daemon thread (exits when main thread exits)
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()
        
        print(f"\n{'='*50}")
        print(f"🌈 WS2812B LED Controller Started")
        print(f"{'='*50}")
        print(f"  Control Panel: http://localhost:{PORT}")
        print(f"  API Docs:      http://localhost:{PORT}/api/docs")
        print(f"  Health Check:  http://localhost:{PORT}/health")
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