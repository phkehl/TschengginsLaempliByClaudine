// LED animations and colours of job states
//
// Shared by all firmwares (no settings, no backend). A firmware selects the animation and applies the level.
//
// - Colour from the result: pass green, fail red, warn yellow, unknown white (LED_COLOUR_... in config.hpp).
// - Level from the status: stopped 1, unknown slow blink (LED_BLINK_MS, half on), running and waiting an animation.
//   Both use the same list ANIMS: first the ones made for running (calm), then the ones made for waiting (nervous).
//
// An animation gives a perceived level (0-1). The LED value is level ^ LED_GAMMA, so the eye sees an even change.
//
// Brightness options (BRIGHT_OPTIONS): for the brightness settings of the firmwares.
//
// Noise (ledNoiseRender()): a random colour on each LED, new each NOISE_MS, as the static noise of a TV. The firmwares
// show it while a firmware update runs.
//
// Needs in config.hpp: LED_COLOUR_PASS, LED_COLOUR_FAIL, LED_COLOUR_WARN, LED_COLOUR_UNKNOWN, LED_BLINK_MS, LED_GAMMA.

#include <Arduino.h>
#include <math.h>

#include "config.hpp"
#include "ledanim.hpp"

// Lowest perceived level of the animations (LED off below about 0.3 at LED_BRIGHTNESS)
static constexpr float LOW_RUNNING = 0.35f;
static constexpr float LOW_WAITING = 0.40f;

/**
 * @brief Get phase of a period
 *
 * @param[in] ms        Time [ms]
 * @param[in] periodMs  Period [ms]
 *
 * @returns the phase (0-1)
 */
static float phase(const uint32_t ms, const uint32_t periodMs)
{
    return ((float)(ms % periodMs) / (float)periodMs);
}

/**
 * @brief Get smooth wave: 0 at phase 0, 1 at phase 0.5, 0 at phase 1
 */
static float wave(const float ph)
{
    return (0.5f - (0.5f * cosf(2.0f * PI * ph)));
}

/**
 * @brief Get level between low and 1
 */
static float between(const float low, const float val)
{
    return (low + ((1.0f - low) * val));
}

/**
 * @brief Get smooth step 0-1 (ease in and out)
 */
static float ease(const float val)
{
    const float x = constrain(val, 0.0f, 1.0f);
    return (x * x * (3.0f - (2.0f * x)));
}

// Cosine on the LED value (first animation, until 2026-10)
static float animOld(const uint32_t ms, const uint32_t periodMs)
{
    const float value = (24.0f + (231.0f * (1.0f - wave(phase(ms, periodMs))))) / 255.0f;
    return powf(value, 1.0f / LED_GAMMA);
}

static float runOld(const uint32_t ms)
{
    return animOld(ms, 1000);
}

static float runBreathe2(const uint32_t ms)
{
    return between(LOW_RUNNING, wave(phase(ms, 2000)));
}

static float runBreathe3(const uint32_t ms)
{
    return between(LOW_RUNNING, wave(phase(ms, 3000)));
}

// Breathing with short peak and long rest: exp(sin) shape
static float runBreatheDeep(const uint32_t ms)
{
    static const float lowExp = expf(-1.0f);
    const float val = (expf(sinf((2.0f * PI * phase(ms, 4000)) - (PI / 2.0f))) - lowExp) / (expf(1.0f) - lowExp);
    return between(LOW_RUNNING, val);
}

// Breathe in 1.2 s, out 1.2 s, rest 0.6 s
static float runBreatheRest(const uint32_t ms)
{
    const uint32_t t = ms % 3000;
    float val = 0.0f;
    if (t < 1200) {
        val = ease((float)t / 1200.0f);
    } else if (t < 2400) {
        val = 1.0f - ease((float)(t - 1200) / 1200.0f);
    }
    return between(LOW_RUNNING, val);
}

static float waitOld(const uint32_t ms)
{
    return animOld(ms, 300);
}

static float waitPulse6(const uint32_t ms)
{
    return between(LOW_WAITING, wave(phase(ms, 600)));
}

static float waitPulse4(const uint32_t ms)
{
    return between(LOW_WAITING, wave(phase(ms, 400)));
}

// Two quick beats, then rest (1 s)
static float waitHeartbeat(const uint32_t ms)
{
    const uint32_t t = ms % 1000;
    float val = 0.0f;
    if (t < 160) {
        val = wave((float)t / 160.0f);
    } else if ((t >= 240) && (t < 400)) {
        val = 0.7f * wave((float)(t - 240) / 160.0f);
    }
    return between(LOW_WAITING, val);
}

// Random flicker (smooth noise, about 6 changes per second)
static float waitFlicker(const uint32_t ms)
{
    const uint16_t noise = inoise16((uint32_t)ms * 400);
    // inoise16 is mostly in 12000-53000
    const float val = constrain(((float)noise - 12000.0f) / 41000.0f, 0.0f, 1.0f);
    return between(LOW_WAITING, val);
}

// Bright with a fast shimmer (8 Hz)
static float waitFlutter(const uint32_t ms)
{
    return between(0.6f, 1.0f - wave(phase(ms, 125)));
}

// Soft blink: 0.25 s on, 0.25 s off, 60 ms fades
static float waitBlink(const uint32_t ms)
{
    const uint32_t t = ms % 500;
    float val = 0.0f;
    if (t < 60) {
        val = ease((float)t / 60.0f);
    } else if (t < 250) {
        val = 1.0f;
    } else if (t < 310) {
        val = 1.0f - ease((float)(t - 250) / 60.0f);
    }
    return between(0.3f, val);
}

// Animation: name and level function
struct Anim
{
    const char* name;
    float (*level)(const uint32_t ms);
};

static const Anim ANIMS[] = {
    { "cosine 1 s", runOld },
    { "breathe 2 s", runBreathe2 },
    { "breathe 3 s", runBreathe3 },
    { "breathe 4 s, deep", runBreatheDeep },
    { "breathe 3 s, rest", runBreatheRest },
    { "cosine 0.3 s", waitOld },
    { "pulse 0.6 s", waitPulse6 },
    { "pulse 0.4 s", waitPulse4 },
    { "heartbeat", waitHeartbeat },
    { "flicker", waitFlicker },
    { "flutter", waitFlutter },
    { "soft blink 0.5 s", waitBlink },
};
static constexpr int NUM_ANIMS = sizeof(ANIMS) / sizeof(ANIMS[0]);

/**
 * @brief Get animation of a status (RUNNING, WAITING: same list)
 *
 * @returns the animation, nullptr if no such animation
 */
static const Anim* getAnim(const LedStatus status, const int ix)
{
    if ((ix < 0) || (ix >= ledAnimCount(status))) {
        return nullptr;
    }
    return &ANIMS[ix];
}

int ledAnimCount(const LedStatus status)
{
    return (((status == LedStatus::RUNNING) || (status == LedStatus::WAITING)) ? NUM_ANIMS : 0);
}

const char* ledAnimName(const LedStatus status, const int ix)
{
    const Anim* anim = getAnim(status, ix);
    return ((anim != nullptr) ? anim->name : "");
}

float ledAnimLevel(const LedStatus status, const int ix, const uint32_t ms)
{
    switch (status) {
        case LedStatus::RUNNING:
        case LedStatus::WAITING: {
            const Anim* anim = getAnim(status, ix);
            return ((anim != nullptr) ? anim->level(ms) : 1.0f);
        }
        case LedStatus::UNKNOWN:
            return (((ms % LED_BLINK_MS) < (LED_BLINK_MS / 2)) ? 1.0f : 0.0f);
        case LedStatus::STOPPED:
            break;
    }
    return 1.0f;
}

uint8_t ledLevelValue(const float level)
{
    return (uint8_t)lroundf(255.0f * powf(constrain(level, 0.0f, 1.0f), LED_GAMMA));
}

CRGB ledStatusColour(const LedResult result)
{
    switch (result) {
        case LedResult::PASS:
            return LED_COLOUR_PASS;
        case LedResult::FAIL:
            return LED_COLOUR_FAIL;
        case LedResult::WARN:
            return LED_COLOUR_WARN;
        case LedResult::UNKNOWN:
            break;
    }
    return LED_COLOUR_UNKNOWN;
}

// LED brightness options (full scale would be 255; the current limit cuts the brightest ones with many white LEDs)
struct Bright
{
    const char* name;
    uint8_t value;
};

static const Bright BRIGHT_OPTIONS[] = {
    { "very dim", 16 },
    { "dim", 32 },
    { "medium", 64 },
    { "bright", 96 },
    { "very bright", 128 },
    { "brightest", 160 },
};
static constexpr int NUM_BRIGHT = sizeof(BRIGHT_OPTIONS) / sizeof(BRIGHT_OPTIONS[0]);

int ledBrightCount()
{
    return NUM_BRIGHT;
}

uint8_t ledBrightOption(const int ix, const char*& name)
{
    if ((ix < 0) || (ix >= NUM_BRIGHT)) {
        name = "";
        return 0;
    }
    name = BRIGHT_OPTIONS[ix].name;
    return BRIGHT_OPTIONS[ix].value;
}

const char* ledBrightName(const uint8_t value)
{
    for (const Bright& option : BRIGHT_OPTIONS) {
        if (option.value == value) {
            return option.name;
        }
    }
    return "custom";
}

// Noise: time of one noise frame [ms]
static constexpr uint32_t NOISE_MS = 40;

/**
 * @brief Mix bits of a value (integer hash)
 */
static uint32_t noiseHash(uint32_t val)
{
    val ^= (val >> 16);
    val *= 0x7feb352dUL;
    val ^= (val >> 15);
    val *= 0x846ca68bUL;
    val ^= (val >> 16);
    return val;
}

void ledNoiseRender(CRGB* leds, const int num, const uint32_t ms)
{
    const uint32_t frame = (ms / NOISE_MS);
    for (int ix = 0; ix < num; ix++) {
        const uint32_t val = noiseHash((frame * 0x9e3779b9UL) ^ (uint32_t)ix);
        leds[ix] = CRGB((uint8_t)(val & 0xff), (uint8_t)((val >> 8) & 0xff), (uint8_t)((val >> 16) & 0xff));
    }
}
