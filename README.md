<img align="right" src="https://visitor-badge.laobi.icu/badge?page_id=platima.Python-WS2812B" height="20" />

# WS2812B LED Controller

A Python-based HTTP server for controlling WS2812B (NeoPixel) RGB LED strips or [Ring LEDs](https://shop.plati.ma/products/ws2812-rgb-led-ring) via SPI interface on nearly any [Single Board Computer (SBC) / eSBC / DevBoard / EvalBoard / ESB](https://github.com/platima/Board-Taxonomies) running Linux, including Busybox/Buildroot!

Tested on [Luckfox Pico Ultra W](https://shop.plati.ma/products/luckfox-pico-ultra) with the Buildroot image and a [MIS5001 Camera](https://shop.plati.ma/products/luckfox-mis5001-5mp-wide-angle-lens-camera-module)

## Features

- 🌐 **Web Interface** - Simple, responsive control panel with color sliders
- 🔌 **REST API** - Control LEDs programmatically via HTTP requests
- 📊 **Health Monitoring** - Real-time system stats and status endpoint
- 🧵 **Thread-Safe** - Proper locking for concurrent access
- 🛡️ **Error Handling** - Graceful error handling and recovery
- 🎨 **Startup Animation** - Ring animation on startup to verify LEDs
- 📖 **API Documentation** - Built-in documentation page

## Hardware Requirements

- SBC (any model with SPI support)
- WS2812B LED strip (NeoPixels)
- Appropriate power supply for your LED strip
- Proper level shifting (3.3V to 5V) if needed

## Software Requirements

- Python 3.10+ (uses native standard library only!)
- SPI enabled on your SBC
- `spidev` Python package (Linux only, see requirements.txt)
- Works on minimal Linux distributions like Buildroot

## Installation

### 1A. Enable SPI on Raspberry Pi

```bash
sudo raspi-config
# Navigate to: Interface Options -> SPI -> Enable
```

### 1B. Enable SPI on Luckfox SBC

```bash
sudo luckfox-config
# Navigate to: Advanced -> SPI -> [Choose your port] -> enable
```
Note: You may need to disable RGB and PWM5

Example Luckfox Pico Ultra W `/etc/luckfox.cfg`
```
SPI0_M0_CS_ENABLE=0
SPI0_M0_MODE=1
RGB_ENABLE=0
TS_ENABLE=0
CSI_ENABLE=1
PWM5_M2_STATUS=0
SPI0_M0_STATUS=1
SPI0_M0_MISO_ENABLE=0
SPI0_M0_SPEED=2400000
USB_MODE=peripheral
```

### 2. Clone Repository

```bash
git clone https://github.com/platima/Python-WS2812B
cd Python-WS2812B
```

### 3. Install Dependencies

```bash
# On Raspberry Pi OS or similar
pip install -r requirements.txt

# On Buildroot or minimal systems, spidev may already be included
# or you may need to install it via your package manager
```

## Configuration

Edit the constants at the top of `ws.py`:

```python
PORT = 8080              # HTTP server port
NUM_LEDS = 16            # Number of LEDs in your strip
DEFAULT_BRIGHTNESS = 64  # Default brightness (0-255)
RING_SPEED = 0.03        # Startup animation speed
```

## Usage

### Start the Server

```bash
python ws.py
```

You should see:

```
✓ SPI initialized successfully
Running startup animation...
✓ Initialized 16 LEDs

==================================================
🌈 WS2812B LED Controller Started
==================================================
  Control Panel: http://localhost:8080
  API Docs:      http://localhost:8080/api/docs
  Health Check:  http://localhost:8080/health
  LEDs:          16 connected
==================================================

Press Ctrl+C to stop the server
```

## API Endpoints

### 🎨 Control Panel
**GET** `/`

Interactive web interface with sliders for RGB control.

### 🔄 Update LEDs
**GET** `/update`

Update LED colors via query parameters.

**Query Parameters:**
- `r` - Red value (0-255, optional)
- `g` - Green value (0-255, optional)
- `b` - Blue value (0-255, optional)

**Examples:**
```bash
# Set all LEDs to red
curl "http://localhost:8080/update?r=255&g=0&b=0"

# Set all LEDs to purple
curl "http://localhost:8080/update?r=128&g=0&b=128"

# Turn off all LEDs
curl "http://localhost:8080/update?r=0&g=0&b=0"
```

### 📊 Health Check
**GET** `/health`

Returns system status and statistics in JSON format.

**Example Response:**
```json
{
  "status": "ok",
  "server_uptime_seconds": 3600.52,
  "server_uptime": "1h 0m 0s",
  "updates_processed": 142,
  "num_leds": 16,
  "current_color": {
    "r": 128,
    "g": 64,
    "b": 255
  },
  "system": {
    "platform": "Linux",
    "platform_release": "5.10.17-v7l+",
    "python_version": "3.10.2",
    "cpu_count": 4,
    "memory": {
      "total_mb": 3906.3,
      "available_mb": 2453.1,
      "used_percent": 37.2
    },
    "load_average": [0.45, 0.52, 0.48],
    "cpu_temp_c": 42.8,
    "system_uptime": "5h 23m"
  }
}
```

**Note:** System stats use native Python and `/proc` filesystem (Linux only). Available stats will vary by platform.

### 📖 API Documentation
**GET** `/api/docs`

Interactive API documentation with examples.

## Wiring Diagram

```
SBC                   WS2812B LED Strip
------------          -----------------
MOSI GPIO ----------> Data In (DIN)
GND ----------------> GND
                      +5V (from separate power supply)
```

**Important:** 
- Use a separate 5V power supply for the LEDs (don't power from Pi)
- Consider using a level shifter for the data line (3.3V → 5V)
- Add a 300-470Ω resistor in series with the data line
- Add a 1000μF capacitor across the LED strip power supply

## Technical Details

### SPI Configuration
- **Bus:** 0
- **Device:** 0
- **Speed:** 2.4 MHz
- **Encoding:** Each bit is represented as 3 SPI bits
  - `0` bit → `0b100` (high-low-low)
  - `1` bit → `0b110` (high-high-low)

### Thread Safety
- All SPI operations are protected by a threading lock
- LED state updates are atomic
- Safe for concurrent HTTP requests

### Error Handling
- SPI initialization failure detection
- Graceful error handling for LED updates
- Automatic cleanup on exit (using atexit)
- Detailed error messages

## Troubleshooting

### SPI Initialization Failed
```
✗ Failed to initialize SPI: [Errno 2] No such file or directory: '/dev/spidev0.0'
```
**Solution:** Enable SPI via `sudo raspi-config`

### Permission Denied
```
✗ Failed to initialize SPI: [Errno 13] Permission denied: '/dev/spidev0.0'
```
**Solution:** Add your user to the SPI group:
```bash
sudo usermod -a -G spi $USER
# Log out and back in
```

### LEDs Show Wrong Colors
- Check wiring (MOSI to DIN)
- Verify power supply voltage (should be 5V)
- Try adjusting SPI speed (2.4 MHz works for most)
- Check if your LEDs are WS2812B (not WS2811 or SK6812)

### LEDs Not Responding
- Check power supply
- Verify SPI is enabled
- Check first LED isn't damaged
- Ensure proper ground connection

## Development

### Running Tests
```bash
# TODO: Add tests
python -m pytest tests/
```

### Code Quality
The code includes:
- Comprehensive docstrings
- Type hints where applicable
- Error handling
- Thread safety measures
- Resource cleanup

## License

See LICENSE file for details.

## Contributing

Pull requests are welcome! Please ensure:
- Code follows existing style
- Docstrings are included
- Error handling is appropriate
- Thread safety is maintained

## Changelog

### v2.0.0 (Current)
- ✅ Fixed race condition in LED state updates
- ✅ Added comprehensive error handling
- ✅ Improved resource management with atexit
- ✅ Added health check endpoint with system stats
- ✅ Added API documentation endpoint
- ✅ Enhanced logging and status messages
- ✅ Native Python system monitoring (no external deps needed!)
- ✅ Improved thread safety
- ✅ Buildroot compatible (minimal dependencies)

### v1.0.0
- Initial release
- Basic web interface
- SPI control of WS2812B LEDs
- Startup animation
