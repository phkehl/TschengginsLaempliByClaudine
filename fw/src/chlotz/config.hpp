// Project configuration

#ifndef CONFIG_HPP
#define CONFIG_HPP

#include <stdint.h>

#include <FastLED.h>

// LED chain wiring (CYD CN1 connector)
static constexpr int LED_DATA_PIN = 27;   // CN1 pin 2, yellow wire
static constexpr int LED_CLOCK_PIN = 22;  // CN1 pin 3, green wire
static constexpr int LED_NUM = 12;        // Number of LED pixels
static constexpr EOrder LED_ORDER = BRG;  // Colour order of the pixels
static constexpr bool LED_REVERSE = false;     // Default: reverse LEDs, flip display (settings)
static constexpr int LED_REVERSE_LEN = LED_NUM;  // Reverse LEDs: all LEDs together

// Brightness and current limit
static constexpr uint8_t LED_BRIGHTNESS = 64;  // Default global brightness (0-255, settings)
static constexpr uint32_t LED_MAX_MA = 200;    // Max. LED current, CN1 3.3V, see laempli/chlotz/chlotz.md

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
static constexpr int LED_INFO_MAX = 6;     // Info lines (display)
static constexpr int LED_PATH_SIZE = 160;  // Job path text

// Current model per LED pixel, in uA, at 3.3 V.
// Measured 2026-10-23, 10 pixels, USB meter.
// The current is linear with the value.
static constexpr uint32_t LED_IDLE_UA  =   600;  // Chip, all channels off
static constexpr uint32_t LED_RED_UA   = 18000;  // Red channel at 255
static constexpr uint32_t LED_GREEN_UA = 11000;  // Green channel at 255
static constexpr uint32_t LED_BLUE_UA  = 10000;  // Blue channel at 255

// Onboard RGB LED (active low, see boardled.cpp)
static constexpr int BOARD_LED_RED_PIN = 4;
static constexpr int BOARD_LED_GREEN_PIN = 16;
static constexpr int BOARD_LED_BLUE_PIN = 17;
static constexpr CRGB BOARD_LED_CONNECTED = CRGB(110, 255, 150);  // Mint green, breathes
static constexpr CRGB BOARD_LED_PROBLEM = CRGB(255, 110, 0);      // Orange, breathes fast
static constexpr CRGB BOARD_LED_OTA = CRGB(40, 120, 255);         // Bright blue, blinks
static constexpr CRGB BOARD_LED_ACTIVITY = CRGB(255, 255, 255);   // White flash
static constexpr uint32_t BOARD_LED_CONNECTED_MS = 3000;          // Breathing period: connected
static constexpr uint32_t BOARD_LED_PROBLEM_MS = 1000;            // Breathing period: problem
static constexpr float BOARD_LED_LOW = 0.3f;                      // Lowest perceived level of breathing
static constexpr uint32_t BOARD_LED_OTA_MS = 200;                 // Blink period: update (half on)
static constexpr uint32_t BOARD_LED_FLASH_MS = 150;               // Activity flash

// Speaker (piezo on P4 "SPEAK"): GPIO26, low pass, amplifier
// SC8002B (always on). Square wave from LEDC, see melody.cpp.
static constexpr int SPEAKER_PIN = 26;
static constexpr uint8_t SPEAKER_CHANNEL = 4;  // LEDC channel 4 (timer 2, not shared)
static constexpr uint8_t SPEAKER_DUTY = 50;    // Volume: duty [%] (1-50, 50 = loudest)

// Display (ILI9341, 240x320, on HSPI)
static constexpr int DISP_SCLK_PIN = 14;
static constexpr int DISP_MOSI_PIN = 13;
static constexpr int DISP_MISO_PIN = 12;
static constexpr int DISP_DC_PIN = 2;
static constexpr int DISP_CS_PIN = 15;
static constexpr int DISP_BL_PIN = 21;             // Backlight, PWM
static constexpr uint8_t DISP_BRIGHTNESS = 128;    // Backlight brightness (0-255)
static constexpr uint32_t DISP_UPDATE_MS = 100;    // Status update interval
static constexpr uint16_t DISP_DIM_TIMEOUT_S = 60;  // Default dim timeout, 0 = never
static constexpr uint8_t DISP_DIM_LEVEL = 20;       // Default dim level [%]
static constexpr uint16_t DISP_SAVER_S = 60;        // Default screensaver timeout, 0 = never
static constexpr uint32_t UI_MENU_TIMEOUT_MS = 5 * 60 * 1000;  // Settings without touch, then main screen

// Touch (XPT2046, on VSPI)
static constexpr int TOUCH_SCLK_PIN = 25;
static constexpr int TOUCH_MOSI_PIN = 32;
static constexpr int TOUCH_MISO_PIN = 39;
static constexpr int TOUCH_CS_PIN = 33;
static constexpr int TOUCH_IRQ_PIN = 36;
static constexpr int TOUCH_CAL_PIN = 0;  // BOOT button, hold at start

// WiFi (station only)
static constexpr int WIFI_MAX_NETS = 6;             // Configured networks, tried in order
static constexpr uint32_t WIFI_CONNECT_MS = 10000;  // Connect timeout per network
static constexpr uint32_t WIFI_SCAN_MS = 15000;     // Scan timeout

// Backend connection
static constexpr uint32_t BACKEND_RETRY_MIN_MS = 5000;   // First retry delay
static constexpr uint32_t BACKEND_RETRY_MAX_MS = 60000;  // Max. retry delay
static constexpr uint32_t BACKEND_TIMEOUT_MS = 45000;    // No data (heartbeat 15 s), reconnect
static constexpr uint32_t BACKEND_MARGIN_MS = 5000;      // Heartbeat margin, then time blinks
static constexpr size_t BACKEND_DATA_MAX = 16384;       // Largest message (state of 12 LEDs)
static constexpr size_t BACKEND_OTA_CHUNK = 8192;          // Firmware update: part size
static constexpr size_t BACKEND_OTA_WINDOW = 2;            // Parts asked ahead
static constexpr uint32_t BACKEND_OTA_TIMEOUT_MS = 15000;  // No part, update fails

#endif // CONFIG_HPP
