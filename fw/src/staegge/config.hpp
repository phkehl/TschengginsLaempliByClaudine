// Project configuration: Stägge (Mini D1 ESP32), see laempli/staegge/staegge.md

#ifndef CONFIG_HPP
#define CONFIG_HPP

#include <stdint.h>

#include <FastLED.h>

// LED chain: WS2801 strip on USB VBUS (5 V). 4 rows of
// 15 LEDs in zig-zag: rows 2 and 4 run backwards.
static constexpr int LED_DATA_PIN = 23;   // D7, VSPI MOSI
static constexpr int LED_CLOCK_PIN = 18;  // D5, VSPI SCK
static constexpr int LED_ROWS = 4;        // Rows (one project each)
static constexpr int LED_ROW_LEN = 15;    // LEDs per row
static constexpr int LED_NUM = (LED_ROWS * LED_ROW_LEN);  // Job LEDs
static constexpr EOrder LED_ORDER = RBG;  // Colour order of the pixels
static constexpr bool LED_REVERSE = false;  // Default: reverse LEDs (settings)
static constexpr int LED_REVERSE_LEN = LED_ROW_LEN;  // Reverse LEDs: in each row

// Brightness and current limit
static constexpr uint8_t LED_BRIGHTNESS = 64;  // Default global brightness (0-255, settings)
static constexpr uint32_t LED_MAX_MA = 600;    // Max. LED current, USB VBUS, see laempli/staegge/staegge.md

// Job status colours and lights (see ledstatus.cpp)
static constexpr CRGB LED_COLOUR_PASS = CRGB(0, 255, 0);
static constexpr CRGB LED_COLOUR_FAIL = CRGB(255, 0, 0);
static constexpr CRGB LED_COLOUR_WARN = CRGB(255, 160, 0);
static constexpr CRGB LED_COLOUR_UNKNOWN = CRGB(255, 255, 255);  // Also: no state yet
static constexpr int LED_ANIM_RUNNING = 3;           // Default animation: job runs ("breathe 4 s, deep")
static constexpr int LED_ANIM_WAITING = 8;           // Default animation: job waits ("heartbeat")
static constexpr float LED_GAMMA = 2.2f;             // LED value = level ^ gamma
static constexpr uint32_t LED_BLINK_MS = 2000;       // Period: status unknown (half on)
static constexpr uint32_t LED_FRAME_MS = 10;         // LED task frame time
static constexpr uint32_t LED_UNKNOWN_AFTER_MS = 5 * 60 * 1000;  // No backend data, then status unknown

// Job info per LED from the backend (memory: LED_NUM times)
static constexpr int LED_INFO_MAX = 0;    // Info lines (no display)
static constexpr int LED_PATH_SIZE = 64;  // Job path text

// Current model per LED pixel, in uA, at 5.0 V.
// Measured 2026-10-29, 8 pixels, USB meter.
// The current is linear with the value.
static constexpr uint32_t LED_IDLE_UA  =   760;  // Chip, all channels off
static constexpr uint32_t LED_RED_UA   = 26000;  // Red channel at 255
static constexpr uint32_t LED_GREEN_UA = 12400;  // Green channel at 255
static constexpr uint32_t LED_BLUE_UA  = 17600;  // Blue channel at 255

// Onboard LED (GPIO2, blue), high active: blinks until the backend is connected
static constexpr uint8_t BOARD_LED_PIN = 2;
static constexpr uint32_t BOARD_LED_BLINK_MS = 1000;  // Period (half on)

// No speaker (melody.cpp: no player)
static constexpr int SPEAKER_PIN = -1;
static constexpr uint8_t SPEAKER_CHANNEL = 4;  // Not used
static constexpr uint8_t SPEAKER_DUTY = 50;    // Not used

// WiFi (station only)
static constexpr int WIFI_MAX_NETS = 6;             // Configured networks, tried in order
static constexpr uint32_t WIFI_CONNECT_MS = 10000;  // Connect timeout per network
static constexpr uint32_t WIFI_SCAN_MS = 15000;     // Scan timeout

// Backend connection
static constexpr uint32_t BACKEND_RETRY_MIN_MS = 5000;   // First retry delay
static constexpr uint32_t BACKEND_RETRY_MAX_MS = 60000;  // Max. retry delay
static constexpr uint32_t BACKEND_TIMEOUT_MS = 45000;    // No data (heartbeat 15 s), reconnect
static constexpr uint32_t BACKEND_MARGIN_MS = 5000;      // Heartbeat margin, then stale
static constexpr size_t BACKEND_DATA_MAX = 49152;       // Largest message (state of 60 LEDs: about 25 kB)
static constexpr size_t BACKEND_OTA_CHUNK = 8192;          // Firmware update: part size
static constexpr size_t BACKEND_OTA_WINDOW = 2;            // Parts asked ahead
static constexpr uint32_t BACKEND_OTA_TIMEOUT_MS = 15000;  // No part, update fails

#endif // CONFIG_HPP
