// Onboard RGB LED: device state, always animated (so one sees that the device runs)
//
// - Connected to the backend (data in time): mint green, breathes (BOARD_LED_CONNECTED_MS).
// - Problem (no WiFi network or backend URL configured, WiFi or backend connecting, connection error, no data from
//   the backend in time, certificate not trusted): orange, breathes fast (BOARD_LED_PROBLEM_MS).
// - Firmware update running: bright blue, blinks (BOARD_LED_OTA_MS).
// - Activity: white flash (BOARD_LED_FLASH_MS) for a change of the LED state from the backend (status, result, info
//   lines, names, LED config) and for settings from the web page.
//
// Breathing: perceived level between BOARD_LED_LOW and 1, gamma corrected (ledLevelValue()), never dark. The colours
// mix the LEDs: the light guide lets little light through, green alone is too weak.
//
// The LED is active low (common anode, CYD): PWM duty = 255 - value. Pins and colours are in config.hpp.

#include <Arduino.h>
#include <math.h>

#include "backend.hpp"
#include "boardled.hpp"
#include "config.hpp"
#include "ledanim.hpp"

// PWM channels (display backlight uses 7)
static constexpr uint8_t CH_RED = 0;
static constexpr uint8_t CH_GREEN = 1;
static constexpr uint8_t CH_BLUE = 2;

// Backend status check interval [ms]
static constexpr uint32_t CHECK_MS = 100;

// Light of the LED, without the flashes
enum class Mode
{
    CONNECTED,
    PROBLEM,
    UPDATE,
};

static CRGB shown(0, 0, 0);

/**
 * @brief Set LED colour (if it changed)
 */
static void setColour(const CRGB colour)
{
    if (colour == shown) {
        return;
    }
    shown = colour;
    ledcWrite(CH_RED, 255 - colour.r);
    ledcWrite(CH_GREEN, 255 - colour.g);
    ledcWrite(CH_BLUE, 255 - colour.b);
}

/**
 * @brief Get breathing colour: smooth wave of the perceived level, gamma corrected
 */
static CRGB breathe(const CRGB colour, const uint32_t ms, const uint32_t periodMs)
{
    const float wave = 0.5f - (0.5f * cosf(2.0f * PI * ((float)(ms % periodMs) / (float)periodMs)));
    CRGB out = colour;
    out.nscale8_video(ledLevelValue(BOARD_LED_LOW + ((1.0f - BOARD_LED_LOW) * wave)));
    return out;
}

void boardLedInit()
{
    const int pins[] = { BOARD_LED_RED_PIN, BOARD_LED_GREEN_PIN, BOARD_LED_BLUE_PIN };
    const uint8_t channels[] = { CH_RED, CH_GREEN, CH_BLUE };
    for (int ix = 0; ix < 3; ix++) {
        ledcSetup(channels[ix], 5000, 8);
        ledcAttachPin(pins[ix], channels[ix]);
        ledcWrite(channels[ix], 255);
    }
}

void boardLedUpdate(const uint32_t ms)
{
    static uint32_t lastCheck = 0;
    static bool first = true;
    static uint32_t activity = 0;
    static uint32_t flashStart = 0;
    static bool flash = false;
    static Mode mode = Mode::PROBLEM;

    // Backend status: mode and flashes
    if (first || ((ms - lastCheck) >= CHECK_MS)) {
        lastCheck = ms;
        const BackendStatus st = backendGetStatus();
        const uint32_t count = st.changes + st.updates + st.settingsApplied;
        if (!first && (count != activity)) {
            flashStart = ms;
            flash = true;
        }
        activity = count;
        first = false;
        if (backendGetOta().state == OtaState::RUNNING) {
            mode = Mode::UPDATE;
        } else if ((st.state == BackendState::CONNECTED) && !st.stale) {
            mode = Mode::CONNECTED;
        } else {
            mode = Mode::PROBLEM;
        }
    }

    if (flash && ((ms - flashStart) < BOARD_LED_FLASH_MS)) {
        setColour(BOARD_LED_ACTIVITY);
        return;
    }
    flash = false;
    switch (mode) {
        case Mode::CONNECTED:
            setColour(breathe(BOARD_LED_CONNECTED, ms, BOARD_LED_CONNECTED_MS));
            break;
        case Mode::PROBLEM:
            setColour(breathe(BOARD_LED_PROBLEM, ms, BOARD_LED_PROBLEM_MS));
            break;
        case Mode::UPDATE:
            setColour(((ms % BOARD_LED_OTA_MS) < (BOARD_LED_OTA_MS / 2)) ? BOARD_LED_OTA : CRGB::Black);
            break;
    }
}
