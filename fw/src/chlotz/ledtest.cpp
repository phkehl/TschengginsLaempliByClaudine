// LED test: sequence of test patterns, rainbow demo
//
// Steps (play: next step after the step time, then again from the start):
//
// - All off, all red, all green, all blue, all white: colour order, current
// - White levels: values 1, 2, 4, ... 255 (scale 255), 1 s each: low end and steps of the brightness
// - Brightness ramp: white, scale 0-255 in 5 s (the current limit cuts the top)
// - Colour wheel: all LEDs, hue goes round in 5 s
// - Rainbow: hue 0-255 from first to last LED: LED order
// - Running light: one white LED goes from first to last
// - Animations: each animation of the list (ledanim.cpp, same for RUNNING and WAITING), ANIM_MS each, LED 1, 2, 3,
//   4, ... in the colours PASS, FAIL, WARN, UNKNOWN, ... (to choose the animations, see LED_ANIM_RUNNING,
//   LED_ANIM_WAITING)
// - LED 1, LED 2, ...: one white LED, 1 s each: count and find each LED
//
// Steps without own scale use the LED brightness setting. The current limit (main.cpp) always applies.
//
// Rainbow demo: a rainbow over all LEDs that moves (DEMO_HUE_MS per hue step), at the LED brightness setting.

#include <Arduino.h>

#include "config.hpp"
#include "chlotzsettings.hpp"
#include "ledstatus.hpp"
#include "ledtest.hpp"

// Kind of step
enum class Kind
{
    SOLID,     // All LEDs one colour
    LEVELS,    // White values 1 ... 255
    RAMP,      // White, scale ramp
    WHEEL,     // All LEDs, hue changes
    RAINBOW,   // Hue over the LEDs
    CHASE,     // Running light
};

// Fixed steps, then one SINGLE step per LED
struct Step
{
    Kind kind;
    const char* name;
    CRGB colour;
    uint32_t timeMs;  // Step time when playing
};

static constexpr uint8_t LEVELS[] = { 1, 2, 4, 8, 16, 32, 64, 128, 255 };
static constexpr int NUM_LEVELS = sizeof(LEVELS) / sizeof(LEVELS[0]);
static constexpr uint32_t LEVEL_MS = 1000;
static constexpr uint32_t CHASE_MS = 250;   // Per LED
static constexpr uint32_t SINGLE_MS = 1000;
static constexpr uint32_t ANIM_MS = 8000;
static constexpr uint32_t DEMO_HUE_MS = 20;  // Rainbow demo speed

static const Step STEPS[] = {
    { Kind::SOLID, "All off", CRGB::Black, 3000 },
    { Kind::SOLID, "All red", CRGB(255, 0, 0), 3000 },
    { Kind::SOLID, "All green", CRGB(0, 255, 0), 3000 },
    { Kind::SOLID, "All blue", CRGB(0, 0, 255), 3000 },
    { Kind::SOLID, "All white", CRGB(255, 255, 255), 3000 },
    { Kind::LEVELS, "White level", CRGB(255, 255, 255), NUM_LEVELS * LEVEL_MS },
    { Kind::RAMP, "Brightness ramp", CRGB(255, 255, 255), 5000 },
    { Kind::WHEEL, "Colour wheel", CRGB::Black, 5000 },
    { Kind::RAINBOW, "Rainbow (LED order)", CRGB::Black, 3000 },
    { Kind::CHASE, "Running light", CRGB(255, 255, 255), 2 * LED_NUM * CHASE_MS },
};
static constexpr int NUM_FIXED = sizeof(STEPS) / sizeof(STEPS[0]);

static bool active = false;
static bool demoActive = false;
static bool playing = false;
static int stepIx = 0;
static uint32_t stepStart = 0;  // Step start [ms], animation time base

/**
 * @brief Get number of animation steps
 */
static int numAnims()
{
    return ledAnimCount(LedStatus::RUNNING);
}

/**
 * @brief Get status and animation index of an animation step
 *
 * @param[in]  ix      Step index
 * @param[out] status  RUNNING (same list as WAITING)
 *
 * @returns the animation index, -1 if not an animation step
 */
static int stepAnim(const int ix, LedStatus& status)
{
    const int anim = ix - NUM_FIXED;
    if ((anim < 0) || (anim >= numAnims())) {
        return -1;
    }
    status = LedStatus::RUNNING;
    return anim;
}

/**
 * @brief Get step time
 */
static uint32_t stepTime(const int ix)
{
    LedStatus status;
    if (ix < NUM_FIXED) {
        return STEPS[ix].timeMs;
    }
    return ((stepAnim(ix, status) >= 0) ? ANIM_MS : SINGLE_MS);
}

void ledTestStart()
{
    active = true;
    playing = true;
    stepIx = 0;
    stepStart = millis();
}

void ledTestStop()
{
    active = false;
}

bool ledTestActive()
{
    return active;
}

void ledTestPlay(const bool play)
{
    // Play gives the step its full time again
    if (play && !playing) {
        stepStart = millis();
    }
    playing = play;
}

bool ledTestPlaying()
{
    return playing;
}

void ledTestStep(const int delta)
{
    stepIx = (stepIx + delta + ledTestNumSteps()) % ledTestNumSteps();
    stepStart = millis();
}

int ledTestIndex()
{
    return stepIx;
}

int ledTestNumSteps()
{
    return (NUM_FIXED + numAnims() + LED_NUM);
}

void ledTestName(char* buf, const size_t size)
{
    LedStatus status;
    const int anim = stepAnim(stepIx, status);
    if (anim >= 0) {
        snprintf(buf, size, "Animation %d: %s", anim, ledAnimName(status, anim));
        return;
    }
    if (stepIx >= NUM_FIXED) {
        snprintf(buf, size, "LED %d", ((stepIx - (NUM_FIXED + numAnims())) + 1));
        return;
    }
    const Step& step = STEPS[stepIx];
    if (step.kind == Kind::LEVELS) {
        const uint32_t ix = ((millis() - stepStart) / LEVEL_MS) % NUM_LEVELS;
        snprintf(buf, size, "%s %u", step.name, LEVELS[ix]);
    } else {
        snprintf(buf, size, "%s", step.name);
    }
}

bool ledTestRender(CRGB* leds, const int num, uint8_t& scale)
{
    if (!active) {
        return false;
    }
    uint32_t elapsed = millis() - stepStart;
    if (playing && (elapsed >= stepTime(stepIx))) {
        ledTestStep(1);
        elapsed = 0;
    }

    scale = settingsLedBrightness();
    fill_solid(leds, num, CRGB::Black);
    LedStatus status;
    const int anim = stepAnim(stepIx, status);
    if (anim >= 0) {
        static const LedResult results[] = { LedResult::PASS, LedResult::FAIL, LedResult::WARN, LedResult::UNKNOWN };
        const uint8_t value = ledAnimValue(status, anim, millis());
        for (int ix = 0; ix < num; ix++) {
            leds[ix] = ledStatusColour(results[ix % 4]);
            leds[ix].nscale8(value);
        }
        return true;
    }
    if (stepIx >= NUM_FIXED) {
        leds[stepIx - (NUM_FIXED + numAnims())] = CRGB(255, 255, 255);
        return true;
    }
    const Step& step = STEPS[stepIx];
    switch (step.kind) {
        case Kind::SOLID:
            fill_solid(leds, num, step.colour);
            break;
        case Kind::LEVELS: {
            const uint8_t val = LEVELS[(elapsed / LEVEL_MS) % NUM_LEVELS];
            fill_solid(leds, num, CRGB(val, val, val));
            scale = 255;
            break;
        }
        case Kind::RAMP:
            fill_solid(leds, num, step.colour);
            scale = (uint8_t)(((elapsed % step.timeMs) * 256) / step.timeMs);
            break;
        case Kind::WHEEL:
            fill_solid(leds, num, CHSV((uint8_t)(((elapsed % step.timeMs) * 256) / step.timeMs), 255, 255));
            break;
        case Kind::RAINBOW:
            fill_rainbow(leds, num, 0, (255 / num));
            break;
        case Kind::CHASE:
            leds[(elapsed / CHASE_MS) % num] = step.colour;
            break;
    }
    return true;
}

void ledDemoStart()
{
    demoActive = true;
}

void ledDemoStop()
{
    demoActive = false;
}

bool ledDemoActive()
{
    return demoActive;
}

void ledDemoRender(CRGB* leds, const int num)
{
    fill_rainbow(leds, num, (uint8_t)(millis() / DEMO_HUE_MS), (255 / max(num, 1)));
}
