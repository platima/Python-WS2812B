import http.server
import socketserver
import urllib.parse
import threading
import spidev
import time

PORT = 80
NUM_LEDS = 16
DEFAULT_BRIGHTNESS = 64
RING_SPEED = 0.03

led_state = {'r': DEFAULT_BRIGHTNESS, 'g': DEFAULT_BRIGHTNESS, 'b': DEFAULT_BRIGHTNESS}
lock = threading.Lock()

# === SPI Setup ===
spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 2400000

BIT_0 = 0b100
BIT_1 = 0b110

LOOKUP = {}
for byte in range(256):
    bits = []
    for i in range(8):
        bit = (byte >> (7 - i)) & 0x01
        bits.append(BIT_1 if bit else BIT_0)
    LOOKUP[byte] = bits

def encode_led_data(rgb_data):
    encoded = []
    for (r, g, b) in rgb_data:
        for byte in (g, r, b):
            encoded.extend(LOOKUP[byte])

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
    if bit_count > 0:
        packed.append(current_byte << (8 - bit_count))
    return packed

def run_ring_animation(spi, num_leds, brightness=64, delay=0.05):
    for i in range(num_leds):
        # Start with all LEDs off
        rgb_list = [(0, 0, 0)] * num_leds

        # Light up one pixel at a time (e.g., white)
        rgb_list[i] = (brightness, brightness, brightness)

        # Encode and send data
        spi_data = [0x00, 0x00, 0x00] + encode_led_data(rgb_list)
        spi.xfer2(spi_data)
        time.sleep(delay)

def update_leds(r, g, b):
    with lock:
        rgb_list = [(r, g, b)] * NUM_LEDS
        spi_data = [0x00, 0x00, 0x00] + encode_led_data(rgb_list)
        spi.xfer2(spi_data)
        time.sleep(0.001)

class LEDRequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/update"):
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            try:
                r = int(query.get("r", [led_state['r']])[0])
                g = int(query.get("g", [led_state['g']])[0])
                b = int(query.get("b", [led_state['b']])[0])
                r = max(0, min(255, r))
                g = max(0, min(255, g))
                b = max(0, min(255, b))
                led_state.update({'r': r, 'g': g, 'b': b})
                update_leds(r, g, b)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'OK')
                return
            except Exception as e:
                self.send_error(400, f"Bad request: {e}")
                return

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
    handler = LEDRequestHandler
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        print(f"Serving on http://localhost:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    # Run ring animation once at startup
    run_ring_animation(spi, NUM_LEDS, DEFAULT_BRIGHTNESS, delay=RING_SPEED)

    # Initialize LEDs to current state
    update_leds(led_state['r'], led_state['g'], led_state['b'])

    # Start the web server in a separate thread    
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        spi.close()
        print("Server stopped.")