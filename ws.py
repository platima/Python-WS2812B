"""
WS2812B LED Controller via SPI Interface
Provides an HTTP web server for controlling RGB LED strips
"""
import http.server
import socketserver
import urllib.parse
import threading
import spidev
import time

# Configuration Constants
PORT = 8080  # HTTP server port (may reuqire root/admin on Linux)
NUM_LEDS = 16  # Number of LEDs in the strip
DEFAULT_BRIGHTNESS = 64  # Default brightness (0-255)
RING_SPEED = 0.03  # Delay between LEDs in startup animation (seconds)

# Global state for current LED color (accessed by multiple threads)
led_state = {'r': DEFAULT_BRIGHTNESS, 'g': DEFAULT_BRIGHTNESS, 'b': DEFAULT_BRIGHTNESS}
lock = threading.Lock()  # Protects SPI communication from concurrent access

# === SPI Setup ===
# WS2812B LEDs use a specific timing protocol that can be approximated via SPI
spi = spidev.SpiDev()
spi.open(0, 0)  # Open SPI bus 0, device 0
spi.max_speed_hz = 2400000  # 2.4 MHz SPI clock for WS2812B timing

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
    """
    with lock:
        # Set all LEDs to the same color
        rgb_list = [(r, g, b)] * NUM_LEDS
        spi_data = [0x00, 0x00, 0x00] + encode_led_data(rgb_list)
        spi.xfer2(spi_data)
        time.sleep(0.001)  # Small delay for LED latch

class LEDRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler for LED control interface."""
    
    def do_GET(self):
        """Handle GET requests for both the web UI and LED updates."""
        # API endpoint for updating LED colors
        if self.path.startswith("/update"):
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            try:
                # Parse RGB values from query string, defaulting to current state
                r = int(query.get("r", [led_state['r']])[0])
                g = int(query.get("g", [led_state['g']])[0])
                b = int(query.get("b", [led_state['b']])[0])
                
                # Clamp values to valid range (0-255)
                r = max(0, min(255, r))
                g = max(0, min(255, g))
                b = max(0, min(255, b))
                
                # Update global state (note: not protected by lock - potential race condition)
                led_state.update({'r': r, 'g': g, 'b': b})
                
                # Send to LEDs (this is protected by lock)
                update_leds(r, g, b)
                
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'OK')
                return
            except Exception as e:
                self.send_error(400, f"Bad request: {e}")
                return

        # Serve the web control interface
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(bytes(f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>RGB + White LED Control</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body {{ font-family: sans-serif; padding: 20px; }}
                    label {{ display: block; margin-top: 10px; }}
                    .slider {{ width: 100%; }}
                </style>
            </head>
            <body>
                <h1>LED Controller</h1>
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
    # Run ring animation once at startup to indicate readiness 
    run_ring_animation(spi, NUM_LEDS, DEFAULT_BRIGHTNESS, delay=RING_SPEED)

    # Initialize LEDs to current state
    update_leds(led_state['r'], led_state['g'], led_state['b'])

    # Start the web server in a daemon thread (exits when main thread exits)
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        # Clean up SPI connection on exit
        spi.close()
        print("\nServer stopped.")