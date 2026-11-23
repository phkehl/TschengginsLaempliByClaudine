// LED status: job state as colour and light
//
// The backend sends for each LED the result of the last finished run of its job and the status of the job (see
// backend.cpp and backend/laempli/jobs.py):
//
// - Colour from the result: pass green, fail red, warn yellow, unknown (no result yet, canceled, unknown job) white.
// - Light from the status: stopped steady, unknown slow blink (LED_BLINK_MS, half on), running and waiting an
//   animation. There are several animations for running and for waiting (see ledanim.cpp), selected with
//   ledAnimSet() (Chlotz Display settings screen, shell "led anim", web page), saved in the LED settings
//   (ledsettings.cpp), default LED_ANIM_RUNNING and LED_ANIM_WAITING. No backend data for LED_UNKNOWN_AFTER_MS:
//   status unknown (see backendGetLedStates()).
// - Unused LEDs are off. Before the first state from the backend, all LEDs blink white.
// - While a firmware update runs: noise on all LEDs (ledNoiseRender()).
//
// The global brightness (settingsLedBrightness(), options ledBrightOption() in ledanim.cpp) scales all job LEDs.
//
// Each LED animates on its own: its animation starts when its status changes (not in phase with the other LEDs).
// Before the first state, all LEDs blink in phase. Colours and animation levels are in ledanim.cpp. The LED task
// (main.cpp) renders LED_FRAME_MS frames, ledout.cpp dithers the output.

#include <Arduino.h>

#include "config.hpp"
#include "ledsettings.hpp"
#include "ledstatus.hpp"

int ledAnimGet(const LedStatus status)
{
    // Saved index not valid (e.g. older firmware): default
    const bool running = (status == LedStatus::RUNNING);
    const int ix = settingsAnim(running);
    if (ix >= ledAnimCount(status)) {
        return (running ? LED_ANIM_RUNNING : LED_ANIM_WAITING);
    }
    return ix;
}

bool ledAnimSet(const LedStatus status, const int ix)
{
    if ((ix < 0) || (ix >= ledAnimCount(status))) {
        return false;
    }
    settingsSetAnim((status == LedStatus::RUNNING), (uint8_t)ix);
    return true;
}

uint8_t ledAnimValue(const LedStatus status, const int ix, const uint32_t ms)
{
    const int count = ledAnimCount(status);
    int use = ix;
    if ((count > 0) && ((ix < 0) || (ix >= count))) {
        use = ledAnimGet(status);
    }
    return ledLevelValue(ledAnimLevel(status, use, ms));
}

void ledStatusRender(CRGB* leds, const int num)
{
    LedState states[LED_NUM];
    const int count = min(num, LED_NUM);
    const uint32_t ms = millis();
    if (backendGetOta().state == OtaState::RUNNING) {
        ledNoiseRender(leds, num, ms);
        return;
    }
    if (!backendGetLedStates(states, count)) {
        fill_solid(leds, num, ledStatusColour(LedResult::UNKNOWN));
        nscale8(leds, num, ledAnimValue(LedStatus::UNKNOWN, -1, ms));
        return;
    }
    // Start time of the animation of each LED: last status change
    static LedStatus lastStatus[LED_NUM];
    static bool lastUsed[LED_NUM] = {};
    static uint32_t startMs[LED_NUM] = {};
    fill_solid(leds, num, CRGB::Black);
    for (int ix = 0; ix < count; ix++) {
        const LedState& state = states[ix];
        if (!state.used || !lastUsed[ix] || (state.status != lastStatus[ix])) {
            startMs[ix] = ms;
        }
        lastUsed[ix] = state.used;
        lastStatus[ix] = state.status;
        if (!state.used) {
            continue;
        }
        leds[ix] = ledStatusColour(state.result);
        leds[ix].nscale8(ledAnimValue(state.status, -1, ms - startMs[ix]));
    }
}
