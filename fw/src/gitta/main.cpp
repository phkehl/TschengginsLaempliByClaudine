// Tschenggins Lämpli "Gitta" on an ESP32 (u-blox NINA-W102): 6 job LEDs, WiFi, buzzer, no display
//
// The LED task (core 1, above the Arduino loop) makes an LED frame each LED_FRAME_MS: the colours (full scale) of the
// job LEDs, then the output in chain order with the global brightness, the current limit and dithering (ledout.cpp).
// The Arduino loop runs WiFi, shell and the status LED. The backend client has its own task (backend.cpp), the
// melodies too (melody.cpp).
//
// Job LEDs: 2 columns of 3 LEDs, in reading order: LED 1 top left, LED 2 top right, ..., LED 6 bottom right (USB
// cable on the left). The chain goes down the left column, then up the right column (chainIndex()). Setting "reverse
// LEDs": turn by 180 deg (USB cable on the right), LED 1 is then at the bottom right.
//
// Job LEDs work as on the Chlotz: same code (ledchain), same settings, same job sounds on the buzzer.
//
// Status LED (orange, on the board):
//
// - Connected to the backend, data in time: heartbeat (two short flashes, then a pause)
// - Busy (WiFi or backend connects, firmware update): slow blink
// - Problem (no WiFi network or backend URL configured, connection error, certificate not trusted, no data in time):
//   fast blink
//
// Firmware update: with the button on the web page (update event, backend.cpp) or the shell command "ota install".
// A new firmware that restarts before its first hello from the backend
// rolls back to the old one (see backend.cpp).
//
// Synced settings: device name, LED brightness, animations, reverse LEDs (ledsettings.cpp).

#include <Arduino.h>
#include <FastLED.h>

#include <atomic>

#include "backend.hpp"
#include "config.hpp"
#include "jobsound.hpp"
#include "ledout.hpp"
#include "ledremote.hpp"
#include "ledsettings.hpp"
#include "ledstatus.hpp"
#include "melody.hpp"
#include "settings.hpp"
#include "shell.hpp"
#include "shellled.hpp"
#include "wifi.hpp"

static constexpr uint32_t DEMO_HUE_MS = 20;  // Rainbow demo speed

static CRGB leds[LED_NUM];   // Colours (full scale), job LED order
static CRGB chain[LED_NUM];  // Colours (full scale), chain order
static std::atomic<uint32_t> ledCurrentMa(0);
static std::atomic<uint8_t> ledScale(0);

// Light of the status LED
enum class StatusLed
{
    CONNECTED,
    BUSY,
    PROBLEM,
};

static void ledTask(void* arg);

bool soundFxPlay()
{
    // No sound module, only melodies
    return false;
}

/**
 * @brief Get chain index of a job LED
 *
 * @param[in] ix  Job LED index (0 = LED 1)
 *
 * @returns the index in the LED chain
 */
static int chainIndex(const int ix)
{
    const int pos = (settingsLedReverse() ? ((LED_NUM - 1) - ix) : ix);
    const int row = (pos / LED_COLS);
    const int col = (pos % LED_COLS);
    if (col == 0) {
        return row;
    }
    return ((LED_NUM - 1) - row);
}

/**
 * @brief Init all modules
 */
void setup()
{
    Serial.begin(115200);
    pinMode(BOARD_LED_PIN, OUTPUT);
    digitalWrite(BOARD_LED_PIN, LOW);
    Serial.printf("\r\nWS2801: %d LEDs, data %d, clock %d, max %d mA\r\n", LED_NUM, LED_DATA_PIN, LED_CLOCK_PIN,
        (int)LED_MAX_MA);

    melodyInit();
    melodyMorse(BOOT_MORSE);
    settingsInit();
    ledSettingsInit();
    ledOutInit();

    wifiInit();
    backendInit();
    shellInit();

    // Above the Arduino loop (priority 1) on the same core
    xTaskCreatePinnedToCore(ledTask, "leds", 4096, nullptr, 2, nullptr, 1);
}

/**
 * @brief Make one LED frame
 *
 * LED output, first one wins: rainbow demo (shell, web), web LED test (ledremote.cpp), shell test colour, job
 * status.
 */
static void ledFrame()
{
    uint32_t currentMa = 0;
    uint8_t usedScale = 0;
    CRGB testColour;
    uint8_t testScale = 0;
    uint8_t scale = settingsLedBrightness();
    const ShellLed shellMode = shellLedMode(testColour, testScale);
    const LedRemoteTest remote = ledRemoteGet();
    if ((shellMode == ShellLed::DEMO) || (remote.mode == LedRemote::DEMO)) {
        fill_rainbow(leds, LED_NUM, (uint8_t)(millis() / DEMO_HUE_MS), (255 / LED_NUM));
    } else if (ledRemoteRender(leds, LED_NUM, remote)) {
        // Brightness setting
    } else if (shellMode == ShellLed::COLOUR) {
        fill_solid(leds, LED_NUM, testColour);
        scale = testScale;
    } else {
        ledStatusRender(leds, LED_NUM);
    }
    for (int ix = 0; ix < LED_NUM; ix++) {
        chain[chainIndex(ix)] = leds[ix];
    }
    ledOutShow(chain, scale, currentMa, usedScale);
    ledCurrentMa = currentMa;
    ledScale = usedScale;
}

/**
 * @brief LED task: one frame each LED_FRAME_MS
 */
static void ledTask(void* arg)
{
    (void)arg;
    TickType_t wake = xTaskGetTickCount();
    while (true) {
        ledFrame();
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(LED_FRAME_MS));
    }
}

/**
 * @brief Get system state for the status LED
 */
static StatusLed statusLedMode()
{
    const BackendStatus st = backendGetStatus();
    if (backendGetOta().state == OtaState::RUNNING) {
        return StatusLed::BUSY;
    }
    if (st.state == BackendState::CONNECTED) {
        return (st.stale ? StatusLed::PROBLEM : StatusLed::CONNECTED);
    }
    if ((wifiGetState() == WifiState::NONE) || (st.state == BackendState::NONE) ||
        (st.state == BackendState::ERROR) || (st.state == BackendState::CERT_UNTRUSTED)) {
        return StatusLed::PROBLEM;
    }
    return StatusLed::BUSY;
}

/**
 * @brief Update status LED
 */
static void statusLedUpdate()
{
    const uint32_t ms = millis();
    bool on = false;
    switch (statusLedMode()) {
        case StatusLed::CONNECTED: {
            const uint32_t phase = (ms % STATUS_LED_HEART_MS);
            on = ((phase < STATUS_LED_BEAT_MS) ||
                  ((phase >= (2 * STATUS_LED_BEAT_MS)) && (phase < (3 * STATUS_LED_BEAT_MS))));
            break;
        }
        case StatusLed::BUSY:
            on = ((ms % STATUS_LED_SLOW_MS) < (STATUS_LED_SLOW_MS / 2));
            break;
        case StatusLed::PROBLEM:
            on = ((ms % STATUS_LED_FAST_MS) < (STATUS_LED_FAST_MS / 2));
            break;
    }
    digitalWrite(BOARD_LED_PIN, (on ? HIGH : LOW));
}

/**
 * @brief Run WiFi, shell and status LED (LEDs, backend and melodies run in their own tasks)
 */
void loop()
{
    wifiUpdate();
    shellLedInfo(ledCurrentMa, ledScale);
    shellUpdate();
    statusLedUpdate();
    delay(10);
}
