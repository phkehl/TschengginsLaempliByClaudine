// Tschenggins Lämpli on an ESP8266: job LEDs, WiFi, piezo speaker, no display
//
// Firmwares: Chlötzli and Chischtli. Same code, own config.hpp (src/<type>/, pins, LEDs, buffers).
//
// The Arduino loop runs WiFi, backend client and shell. The LEDs run as scheduled function each LED_FRAME_MS: the
// ESP8266 core runs it after each loop and at each yield() and delay(), so the animation continues while the backend
// client waits for the network. TLS handshakes do not yield (about 1 s, the LEDs stop).
//
// Job LEDs: the chain starts at the bottom. Setting "reverse LEDs" (default LED_REVERSE): LED 1 is the top one.
//
// Job LEDs work as on the Chlotz: same code (ledchain), same settings, same job sounds on the speaker.
//
// Status LED (onboard LED), also in the scheduled function:
//
// - Connected to the backend, data in time: heartbeat (two short flashes, then a pause)
// - Busy (WiFi or backend connects, firmware update): slow blink
// - Problem (no WiFi network or backend URL configured, connection error, certificate not trusted, no data in time):
//   fast blink
//
// Piezo speaker: melodies (melody.cpp, scheduled function as the LEDs), shell command "melody".
//
// Firmware update: with the button on the web page (update event, backend.cpp) or the shell command "ota install".
// There is no rollback on the ESP8266: a firmware that does not start needs
// the USB cable.
//
// Synced settings: device name, LED brightness, animations, reverse LEDs (ledsettings.cpp).

#include <Arduino.h>
#include <FastLED.h>
#include <Schedule.h>

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

static CRGB leds[LED_NUM];  // Colours (full scale)

// Light of the status LED
enum class StatusLed
{
    CONNECTED,
    BUSY,
    PROBLEM,
};

bool soundFxPlay()
{
    // No sound module, only melodies
    return false;
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
    digitalWrite(BOARD_LED_PIN, (on ? LOW : HIGH));
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
    ledOutShow(leds, scale, currentMa, usedScale);
    shellLedInfo(currentMa, usedScale);
}

/**
 * @brief Init all modules
 */
void setup()
{
    Serial.begin(115200);
    pinMode(BOARD_LED_PIN, OUTPUT);
    digitalWrite(BOARD_LED_PIN, HIGH);
    Serial.printf("\r\nWS2801: %d LEDs, data %d, clock %d, max %d mA\r\n", LED_NUM, LED_DATA_PIN, LED_CLOCK_PIN,
        (int)LED_MAX_MA);

    melodyInit();
    melodyMorse(BOOT_MORSE);
    settingsInit();
    ledSettingsInit();
    ledOutInit();
    schedule_recurrent_function_us([]() {
        ledFrame();
        statusLedUpdate();
        return true;
    }, LED_FRAME_MS * 1000);

    wifiInit();
    backendInit();
    shellInit();
}

void loop()
{
    wifiUpdate();
    backendUpdate();
    shellUpdate();
    delay(5);
}
