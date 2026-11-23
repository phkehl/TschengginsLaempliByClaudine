// Tschenggins Lämpli on the Chlotz: LEDs, display, WiFi
//
// The LED task (core 1, above the Arduino loop) makes an LED frame each LED_FRAME_MS: the colours (full scale), then
// the output with the global brightness, the current limit and dithering (ledout.cpp). It also updates the onboard RGB
// LED (boardled.cpp). The melody task plays the melodies and the boot sound (melody.cpp). The Arduino loop runs WiFi,
// UI and shell.

#include <Arduino.h>
#include <FastLED.h>

#include <atomic>

#include "backend.hpp"
#include "boardled.hpp"
#include "config.hpp"
#include "display.hpp"
#include "jobsound.hpp"
#include "ledout.hpp"
#include "ledremote.hpp"
#include "ledstatus.hpp"
#include "ledtest.hpp"
#include "melody.hpp"
#include "chlotzsettings.hpp"
#include "settings.hpp"
#include "shell.hpp"
#include "shellled.hpp"
#include "ui.hpp"
#include "wifi.hpp"

static CRGB leds[LED_NUM];     // Colours (full scale), also for the UI
static std::atomic<uint32_t> ledCurrentMa(0);
static std::atomic<uint8_t> ledScale(0);

static void ledTask(void* arg);

bool soundFxPlay()
{
    // No sound module
    return false;
}

/**
 * @brief Init all modules
 */
void setup()
{
    Serial.begin(115200);
    Serial.printf("WS2801: %d LEDs, data %d, clock %d, max %d mA\r\n", LED_NUM, LED_DATA_PIN, LED_CLOCK_PIN,
        LED_MAX_MA);

    settingsInit();
    chlotzSettingsInit();
    displayInit();
    ledOutInit();

    wifiInit();
    backendInit();
    shellInit();
    boardLedInit();
    melodyInit();
    melodyMorse(BOOT_MORSE);

    // Above the Arduino loop (priority 1) on the same core
    xTaskCreatePinnedToCore(ledTask, "leds", 4096, nullptr, 2, nullptr, 1);
}

/**
 * @brief Make one LED frame
 *
 * LED output, first one wins: LED test (UI), rainbow demo (UI, shell, web), web LED test (ledremote.cpp), shell
 * "led" command, job status.
 */
static void ledFrame()
{
    uint32_t currentMa = 0;
    uint8_t usedScale = 0;
    CRGB testColour;
    uint8_t testScale = 0;
    const ShellLed shellMode = shellLedMode(testColour, testScale);
    const LedRemoteTest remote = ledRemoteGet();
    if (ledTestRender(leds, LED_NUM, testScale)) {
        ledOutShow(leds, testScale, currentMa, usedScale);
    } else if (ledDemoActive() || (shellMode == ShellLed::DEMO) || (remote.mode == LedRemote::DEMO)) {
        ledDemoRender(leds, LED_NUM);
        ledOutShow(leds, settingsLedBrightness(), currentMa, usedScale);
    } else if (ledRemoteRender(leds, LED_NUM, remote)) {
        ledOutShow(leds, settingsLedBrightness(), currentMa, usedScale);
    } else if (shellMode == ShellLed::COLOUR) {
        fill_solid(leds, LED_NUM, testColour);
        ledOutShow(leds, testScale, currentMa, usedScale);
    } else {
        ledStatusRender(leds, LED_NUM);
        ledOutShow(leds, settingsLedBrightness(), currentMa, usedScale);
    }
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
        boardLedUpdate(millis());
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(LED_FRAME_MS));
    }
}

/**
 * @brief Run WiFi, UI and shell (the LEDs run in their own task)
 */
void loop()
{
    const uint32_t currentMa = ledCurrentMa;
    const uint8_t usedScale = ledScale;
    wifiUpdate();
    uiUpdate(leds, LED_NUM, currentMa, usedScale);
    shellLedInfo(currentMa, usedScale);
    shellUpdate();
    delay(10);
}
