// Project configuration: Gitta (ESP32, u-blox NINA-W102), see laempli/gitta/gitta.md

#ifndef CONFIG_HPP
#define CONFIG_HPP

#include <stdint.h>

#include <FastLED.h>

// LED chain: 6 WS2801 LEDs, 2 columns of 3 rows.
// Chain: left column down, right column up.
static constexpr int LED_DATA_PIN = 19;   // Header "DI"
static constexpr int LED_CLOCK_PIN = 18;  // Header "CI"
static constexpr int LED_COLS = 2;        // Columns
static constexpr int LED_ROWS = 3;        // Rows
static constexpr int LED_NUM = (LED_COLS * LED_ROWS);  // Job LEDs
static constexpr EOrder LED_ORDER = RGB;  // Colour order (checked 2026-11-12)
static constexpr bool LED_REVERSE = false;  // Default: reverse LEDs, turn by 180 deg (settings)
static constexpr int LED_REVERSE_LEN = 1;   // Not in ledout.cpp: main.cpp turns the LEDs

// Brightness and current limit
static constexpr uint8_t LED_BRIGHTNESS = 64;  // Default global brightness (0-255, settings)
static constexpr uint32_t LED_MAX_MA = 300;    // Max. LED current (USB powered)

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
static constexpr int LED_INFO_MAX = 6;     // Info lines (shell)
static constexpr int LED_PATH_SIZE = 160;  // Job path text

// Current model per LED pixel, in uA, at 5.0 V.
// Values of the Stägge strip, not measured here.
static constexpr uint32_t LED_IDLE_UA  =   760;  // Chip, all channels off
static constexpr uint32_t LED_RED_UA   = 26000;  // Red channel at 255
static constexpr uint32_t LED_GREEN_UA = 12400;  // Green channel at 255
static constexpr uint32_t LED_BLUE_UA  = 17600;  // Blue channel at 255

// Status LED (GPIO26, orange, high active): system status, see main.cpp
static constexpr uint8_t BOARD_LED_PIN = 26;
static constexpr uint32_t STATUS_LED_HEART_MS = 1500;  // Connected: period of two flashes
static constexpr uint32_t STATUS_LED_BEAT_MS = 100;    // Connected: flash and gap time
static constexpr uint32_t STATUS_LED_SLOW_MS = 2000;   // Busy: period (half on)
static constexpr uint32_t STATUS_LED_FAST_MS = 200;    // Problem: period (half on)

// Buzzer (GPIO27), square wave, see melody.cpp
static constexpr int SPEAKER_PIN = 27;
static constexpr uint8_t SPEAKER_CHANNEL = 4;  // LEDC channel 4 (timer 2, not shared)
static constexpr uint8_t SPEAKER_DUTY = 50;    // Volume: duty [%] (1-50, 50 = loudest)

// WiFi (station only)
static constexpr int WIFI_MAX_NETS = 6;             // Configured networks, tried in order
static constexpr uint32_t WIFI_CONNECT_MS = 10000;  // Connect timeout per network
static constexpr uint32_t WIFI_SCAN_MS = 15000;     // Scan timeout

// Backend connection
static constexpr uint32_t BACKEND_RETRY_MIN_MS = 5000;   // First retry delay
static constexpr uint32_t BACKEND_RETRY_MAX_MS = 60000;  // Max. retry delay
static constexpr uint32_t BACKEND_TIMEOUT_MS = 45000;    // No data (heartbeat 15 s), reconnect
static constexpr uint32_t BACKEND_MARGIN_MS = 5000;      // Heartbeat margin, then stale
static constexpr size_t BACKEND_DATA_MAX = 16384;        // Largest message (about 1 kB per LED)
static constexpr size_t BACKEND_OTA_CHUNK = 8192;          // Firmware update: part size
static constexpr size_t BACKEND_OTA_WINDOW = 2;            // Parts asked ahead
static constexpr uint32_t BACKEND_OTA_TIMEOUT_MS = 15000;  // No part, update fails

#endif // CONFIG_HPP
