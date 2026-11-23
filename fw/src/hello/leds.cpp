// LED ring on the cylinder: job state
//
// The chain is strip A (LEDs 0-5) then strip B (LEDs 6-11). Each strip goes round the cylinder, 60 deg from LED to LED.
// Strip B is offset by 30 deg, so the ring has 12 positions: A0 B0 A1 B1 ... (as old firmware, ledsSetStateHello()).
// LED 12 (LED_BOW_IX) is the bow.
//
// This Lämpli shows one job (hard-coded effects, no settings), from the backend (LED 1). Colours and animations as on
// the Chlotz (ledanim.cpp):
//
// | Status  | Ring (result colour)    | Bow (white)                    |
// |---------|-------------------------|--------------------------------|
// | STOPPED | steady                  | off                            |
// | RUNNING | light spot goes round   | "breathe 3 s, rest"            |
// | WAITING | light spot goes round   | "heartbeat"                    |
// | UNKNOWN | steady                  | blink (LED_BLINK_MS, half on)  |
//
// Before the first state from the backend: UNKNOWN result and status. LED not configured on the backend: all off.
// The shell can set a test state (ledsTestState()). While a firmware update runs: noise on all LEDs, ring and bow
// (ledNoiseRender()). LED test of the web page (ledremote.cpp): colour on all LEDs, rainbow on the ring, find LED 1:
// all LEDs blink white.
//
// Brightness: the ring and the bow have their own (settings, hellosettings.cpp). They scale all output, also the tests.
//
// Round light: a light spot turns once each ROUND_PERIOD_MS, setting "reverse LEDs": the other way. The level of each
// LED is a cosine of its angle to the spot (1 at the spot, 0 at ROUND_WIDTH_DEG and more). The LED value is
// level ^ LED_GAMMA, so the eye sees an even change.

#include <Arduino.h>
#include <FastLED.h>
#include <math.h>

#include "backend.hpp"
#include "config.hpp"
#include "hellosettings.hpp"
#include "ledanim.hpp"
#include "ledremote.hpp"
#include "leds.hpp"

static CRGB leds[LED_PIXELS];
static bool testOn = false;
static LedState testState = { true, LedResult::UNKNOWN, LedStatus::UNKNOWN };
static int singleIx = -1;
static bool colourOn = false;
static CRGB colourAll;
static uint32_t lastFrame = 0;

static constexpr uint32_t DEMO_HUE_MS = 20;  // Rainbow speed (web LED test)

void ledsInit()
{
    FastLED.addLeds<WS2801, LED_DATA_PIN, LED_CLOCK_PIN, LED_ORDER, DATA_RATE_MHZ(1)>(leds, LED_PIXELS);
    FastLED.clear(true);
}

float ledAngle(const int ix)
{
    if (ix < LED_STRIP_NUM) {
        return (ix * (360.0f / LED_STRIP_NUM));
    }
    return (((ix - LED_STRIP_NUM) * (360.0f / LED_STRIP_NUM)) + (180.0f / LED_STRIP_NUM));
}

/**
 * @brief Render round light
 *
 * @param[in] colour  Colour of the light spot
 * @param[in] ms      Time [ms]
 */
static void renderRound(const CRGB colour, const uint32_t ms)
{
    const float angle = (360.0f * (float)(ms % ROUND_PERIOD_MS)) / (float)ROUND_PERIOD_MS;
    const float spot = (settingsLedReverse() ? (360.0f - angle) : angle);
    for (int ix = 0; ix < LED_RING_NUM; ix++) {
        float dist = fabsf(ledAngle(ix) - spot);
        if (dist > 180.0f) {
            dist = 360.0f - dist;
        }
        float level = 0.0f;
        if (dist < ROUND_WIDTH_DEG) {
            level = 0.5f * (1.0f + cosf((float)M_PI * (dist / ROUND_WIDTH_DEG)));
        }
        leds[ix] = colour;
        leds[ix].nscale8_video(ledLevelValue(level));
    }
}

/**
 * @brief Render job state
 *
 * @param[in] ms  Time [ms]
 */
static void renderState(const uint32_t ms)
{
    const LedState state = ledsGetState();
    if (!state.used) {
        fill_solid(leds, LED_PIXELS, CRGB::Black);
        return;
    }
    const LedStatus status = state.status;

    // Ring
    const CRGB colour = ledStatusColour(state.result);
    if ((status == LedStatus::RUNNING) || (status == LedStatus::WAITING)) {
        renderRound(colour, ms);
    } else {
        fill_solid(leds, LED_RING_NUM, colour);
    }

    // Bow
    uint8_t bow = 0;
    switch (status) {
        case LedStatus::STOPPED:
            break;
        case LedStatus::RUNNING:
            bow = ledLevelValue(ledAnimLevel(status, BOW_ANIM_RUNNING, ms));
            break;
        case LedStatus::WAITING:
            bow = ledLevelValue(ledAnimLevel(status, BOW_ANIM_WAITING, ms));
            break;
        case LedStatus::UNKNOWN:
            bow = ledLevelValue(ledAnimLevel(status, -1, ms));
            break;
    }
    leds[LED_BOW_IX] = BOW_COLOUR;
    leds[LED_BOW_IX].nscale8_video(bow);
}

void ledsUpdate()
{
    const uint32_t now = millis();
    if ((now - lastFrame) < LED_FRAME_MS) {
        return;
    }
    lastFrame = now;

    const LedRemoteTest remote = ledRemoteGet();
    if (colourOn) {
        fill_solid(leds, LED_PIXELS, colourAll);
    } else if (singleIx >= 0) {
        fill_solid(leds, LED_PIXELS, CRGB::Black);
        leds[singleIx] = CRGB(255, 255, 255);
    } else if (backendGetOta().state == OtaState::RUNNING) {
        ledNoiseRender(leds, LED_PIXELS, now);
    } else if (remote.mode == LedRemote::DEMO) {
        fill_rainbow(leds, LED_RING_NUM, (uint8_t)(now / DEMO_HUE_MS), (255 / LED_RING_NUM));
        leds[LED_BOW_IX] = CRGB::Black;
    } else if (remote.mode == LedRemote::FIND) {
        // One job LED: all pixels
        fill_solid(leds, LED_PIXELS, (ledRemoteFindOn(remote, 0) ? CRGB(255, 255, 255) : CRGB(0, 0, 0)));
    } else if (ledRemoteRender(leds, LED_PIXELS, remote)) {
        // Colour on all pixels
    } else {
        renderState(now);
    }

    // Brightness of ring and bow
    const uint8_t ring = settingsRingBrightness();
    for (int ix = 0; ix < LED_RING_NUM; ix++) {
        leds[ix].nscale8_video(ring);
    }
    leds[LED_BOW_IX].nscale8_video(settingsBowBrightness());
    FastLED.show();
}

LedState ledsGetState()
{
    if (testOn) {
        return testState;
    }
    LedState state;
    if (!backendGetLedStates(&state, 1)) {
        state = { true, LedResult::UNKNOWN, LedStatus::UNKNOWN };
    }
    return state;
}

void ledsTestState(const LedResult result, const LedStatus status)
{
    testState = { true, result, status };
    testOn = true;
    colourOn = false;
    singleIx = -1;
}

void ledsNormal()
{
    testOn = false;
    colourOn = false;
    singleIx = -1;
}

void ledsSingle(const int ix)
{
    colourOn = false;
    singleIx = (((ix >= 0) && (ix < LED_PIXELS)) ? ix : -1);
}

void ledsColour(const CRGB colour)
{
    colourAll = colour;
    colourOn = true;
}
