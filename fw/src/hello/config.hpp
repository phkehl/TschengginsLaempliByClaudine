// Project configuration: "hello" (Wemos D1 mini)

#ifndef CONFIG_HPP
#define CONFIG_HPP

#include <stdint.h>

#include <FastLED.h>

// Onboard LED (D4), low active: blinks until the backend is connected
static constexpr uint8_t BOARD_LED_PIN = 2;
static constexpr uint32_t BOARD_LED_BLINK_MS = 1000;  // Period (half on)

// Sound module (D1): trigger pulse, high active
static constexpr uint8_t SOUND_PIN = 5;
static constexpr uint32_t SOUND_PULSE_MS = 2000;  // As old firmware (statusFx())

// Piezo speaker (D2), square wave, see melody.cpp
static constexpr int SPEAKER_PIN = 4;
static constexpr uint8_t SPEAKER_DUTY = 50;  // Volume: duty [%] (1-50, 50 = loudest)

// LED ring: two WS2801 strips of 6 LEDs around a cylinder (55 mm). Strip B
// is offset by half an LED distance: LED angles A0 0, B0 30, A1 60, ... deg.
// Then one more LED: the bow. Pins as old firmware (HSPI pins, bit-banged).
static constexpr int LED_DATA_PIN = 13;   // D7
static constexpr int LED_CLOCK_PIN = 14;  // D5
static constexpr int LED_STRIP_NUM = 6;   // LEDs per strip
static constexpr int LED_RING_NUM = (2 * LED_STRIP_NUM);
static constexpr int LED_BOW_IX = LED_RING_NUM;  // Bow LED, after the ring
static constexpr int LED_PIXELS = (LED_RING_NUM + 1);
static constexpr EOrder LED_ORDER = RBG;      // Found with colour test
static constexpr uint8_t RING_BRIGHTNESS = 128;  // Default ring brightness (1-255, settings)
static constexpr uint8_t BOW_BRIGHTNESS = 128;   // Default bow brightness (1-255, settings)
static constexpr bool LED_REVERSE = false;       // Default: light spot goes the other way (settings)
static constexpr float LED_GAMMA = 2.2f;      // LED value = level ^ gamma
static constexpr uint32_t LED_FRAME_MS = 10;  // Frame time

// Job state (one job, hard-coded, no settings), see leds.cpp:
// ring in the result colour, steady or a light spot that goes round.
// Bow LED white: off, animation or blink.
static constexpr CRGB LED_COLOUR_PASS = CRGB(0, 255, 0);
static constexpr CRGB LED_COLOUR_FAIL = CRGB(255, 0, 0);
static constexpr CRGB LED_COLOUR_WARN = CRGB(255, 160, 0);
static constexpr CRGB LED_COLOUR_UNKNOWN = CRGB(255, 255, 255);
static constexpr uint32_t LED_BLINK_MS = 2000;     // Period: status unknown (half on)
static constexpr uint32_t ROUND_PERIOD_MS = 2000;  // Time for one turn
static constexpr float ROUND_WIDTH_DEG = 60.0f;    // Half width of the light spot
static constexpr CRGB BOW_COLOUR = CRGB(255, 255, 255);
static constexpr int BOW_ANIM_RUNNING = 4;  // "breathe 3 s, rest" (ledanim.cpp)
static constexpr int BOW_ANIM_WAITING = 8;  // "heartbeat" (ledanim.cpp)

// Job LEDs for the backend: one job (ring and bow show it)
static constexpr int LED_NUM = 1;
static constexpr uint32_t LED_UNKNOWN_AFTER_MS = 5 * 60 * 1000;  // No backend data, then status unknown
static constexpr int LED_INFO_MAX = 6;     // Info lines per job (shell)
static constexpr int LED_PATH_SIZE = 160;  // Job path text

// WiFi (station only)
static constexpr int WIFI_MAX_NETS = 6;             // Configured networks, tried in order
static constexpr uint32_t WIFI_CONNECT_MS = 10000;  // Connect timeout per network
static constexpr uint32_t WIFI_SCAN_MS = 15000;     // Scan timeout

// Backend connection. TLS receive buffer: the server
// has no max. fragment length, its largest handshake
// record is the RSA certificate chain, 4.1 kB (2026-11).
// BearSSL takes records up to the buffer size - 5.
static constexpr uint32_t BACKEND_RETRY_MIN_MS = 5000;   // First retry delay
static constexpr uint32_t BACKEND_RETRY_MAX_MS = 60000;  // Max. retry delay
static constexpr uint32_t BACKEND_TIMEOUT_MS = 45000;    // No data (heartbeat 15 s), reconnect
static constexpr uint32_t BACKEND_MARGIN_MS = 5000;      // Heartbeat margin, then stale
static constexpr size_t BACKEND_DATA_MAX = 2048;         // Largest message (state of one LED: about 1 kB)
static constexpr int BACKEND_TLS_RX = 4608;              // TLS receive buffer (WebSocket)
static constexpr int BACKEND_TLS_TX = 512;               // TLS send buffer
static constexpr size_t BACKEND_OTA_CHUNK = 1024;          // Firmware update: part size
static constexpr size_t BACKEND_OTA_WINDOW = 2;            // Parts asked ahead
static constexpr uint32_t BACKEND_OTA_TIMEOUT_MS = 15000;  // No part, update fails

#endif // CONFIG_HPP
