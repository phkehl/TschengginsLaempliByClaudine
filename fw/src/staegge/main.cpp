// Tschenggins Lämpli "Stägge" on the Mini D1 ESP32: 4 rows of 15 job LEDs, WiFi, no display, no speaker
//
// The LED task (core 1, above the Arduino loop) makes an LED frame each LED_FRAME_MS: the colours (full scale) of the
// job LEDs, then the output in chain order with the global brightness, the current limit and dithering (ledout.cpp).
// The current limit (LED_MAX_MA) lowers the brightness of all LEDs if needed. The Arduino loop runs WiFi, shell, the
// onboard LED and the firmware update. The backend client has its own task (backend.cpp).
//
// Job LEDs: the backend numbers them 1-60, row 1 is LED 1-15, row 2 is LED 16-30, ... The strip runs in zig-zag:
// rows 2 and 4 run backwards, thus LED 1 of each row is on the same side (chainIndex()).
//
// Onboard LED: blinks until the backend is connected, then off.
//
// Firmware update: with the button on the web page (update event, backend.cpp) or the shell command "ota install".
// A new firmware that restarts before its first hello from the backend
// rolls back to the old one (see backend.cpp).
//
// Synced settings: device name, LED brightness and animations (ledsettings.cpp).

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
#include "settings.hpp"
#include "shell.hpp"
#include "shellled.hpp"
#include "wifi.hpp"

static constexpr uint32_t DEMO_HUE_MS = 20;  // Rainbow demo speed

static CRGB leds[LED_NUM];   // Colours (full scale), job LED order
static CRGB chain[LED_NUM];  // Colours (full scale), chain order
static std::atomic<uint32_t> ledCurrentMa(0);
static std::atomic<uint8_t> ledScale(0);

static void ledTask(void* arg);

bool soundFxPlay()
{
    // No sound module
    return false;
}

/**
 * @brief Get chain index of a job LED
 *
 * @param[in] ix  Job LED index (0 = LED 1)
 *
 * @returns the index in the LED chain (zig-zag rows)
 */
static int chainIndex(const int ix)
{
    const int row = (ix / LED_ROW_LEN);
    const int col = (ix % LED_ROW_LEN);
    if ((row % 2) == 0) {
        return ix;
    }
    return ((row * LED_ROW_LEN) + (LED_ROW_LEN - 1 - col));
}

/**
 * @brief Init all modules
 */
void setup()
{
    Serial.begin(115200);
    pinMode(BOARD_LED_PIN, OUTPUT);
    digitalWrite(BOARD_LED_PIN, LOW);
    Serial.printf("\r\nWS2801: %d LEDs (%d x %d), data %d, clock %d, max %d mA\r\n", LED_NUM, LED_ROWS, LED_ROW_LEN,
        LED_DATA_PIN, LED_CLOCK_PIN, LED_MAX_MA);

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
        // Same rainbow on each row: one colour per column
        for (int row = 0; row < LED_ROWS; row++) {
            fill_rainbow(&leds[row * LED_ROW_LEN], LED_ROW_LEN, (uint8_t)(millis() / DEMO_HUE_MS),
                (255 / LED_ROW_LEN));
        }
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
 * @brief Update onboard LED
 */
static void boardLedUpdate()
{
    const bool connected = (backendGetStatus().state == BackendState::CONNECTED);
    const bool on = (!connected && ((millis() % BOARD_LED_BLINK_MS) < (BOARD_LED_BLINK_MS / 2)));
    digitalWrite(BOARD_LED_PIN, (on ? HIGH : LOW));
}

/**
 * @brief Run WiFi, shell and onboard LED (the LEDs and the backend run in their own tasks)
 */
void loop()
{
    wifiUpdate();
    shellLedInfo(ledCurrentMa, ledScale);
    shellUpdate();
    boardLedUpdate();
    delay(10);
}
