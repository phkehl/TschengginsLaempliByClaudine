// LED output: WS2801 chain with current limit and dithering
//
// Current limit: the output estimates the current of all LEDs from the RGB values (current model LED_IDLE_UA,
// LED_RED_UA, ..., measured, linear with the value). Above LED_MAX_MA it lowers the global brightness of all LEDs (one
// scale for all, so the colours and the relation between the LEDs stay the same).
//
// Dithering: the rest below one LED step carries over to the next frame, so slow fades are smooth also at low
// brightness. Call ledOutShow() for each frame (LED_FRAME_MS).
//
// Reverse LEDs (setting): each group of LED_REVERSE_LEN LEDs in the chain goes the other way. The groups stay in place.
// All LEDs are one group on the Chlotz and the Chlötzli, each row is one group on the Stägge.

#include <Arduino.h>

#include "config.hpp"
#include "ledout.hpp"
#include "ledsettings.hpp"

static CRGB output[LED_NUM];       // LED output (brightness applied, dithered)
static uint8_t rest[LED_NUM][3];   // Dither rest of each channel (1/256 steps)

/**
 * @brief Estimate current of all LEDs
 *
 * @param[in] leds   Colours (full scale)
 * @param[in] scale  Global brightness (0-255)
 *
 * @returns the estimated current in uA
 */
static uint32_t estimateCurrent(const CRGB* leds, const uint8_t scale)
{
    uint64_t sumRed = 0;
    uint64_t sumGreen = 0;
    uint64_t sumBlue = 0;
    for (int ix = 0; ix < LED_NUM; ix++) {
        sumRed += leds[ix].r;
        sumGreen += leds[ix].g;
        sumBlue += leds[ix].b;
    }
    const uint64_t channelsUa = (sumRed * LED_RED_UA) + (sumGreen * LED_GREEN_UA) + (sumBlue * LED_BLUE_UA);
    return (LED_NUM * LED_IDLE_UA) + (uint32_t)((channelsUa * scale) / (255 * 255));
}

/**
 * @brief Get reversed chain index
 *
 * @param[in] ix  Chain index
 *
 * @returns the index with the other direction in its group
 */
static int reverseIndex(const int ix)
{
    return ((ix - (ix % LED_REVERSE_LEN)) + ((LED_REVERSE_LEN - 1) - (ix % LED_REVERSE_LEN)));
}

void ledOutInit()
{
    // Lower clock helps long wires
    FastLED.addLeds<WS2801, LED_DATA_PIN, LED_CLOCK_PIN, LED_ORDER, DATA_RATE_MHZ(1)>(output, LED_NUM);
    FastLED.setDither(DISABLE_DITHER);
}

void ledOutShow(const CRGB* leds, const uint8_t scale, uint32_t& currentMa, uint8_t& usedScale)
{
    static constexpr uint32_t maxUa = LED_MAX_MA * 1000;
    static constexpr uint32_t idleUa = LED_NUM * LED_IDLE_UA;
    uint8_t useScale = scale;
    uint32_t currentUa = estimateCurrent(leds, scale);

    // Reduce brightness if over limit
    if (currentUa > maxUa) {
        useScale = (uint8_t)(((uint64_t)scale * (maxUa - idleUa)) / (currentUa - idleUa));
        currentUa = estimateCurrent(leds, useScale);
    }
    // Output with dithering
    const bool reverse = settingsLedReverse();
    for (int ix = 0; ix < LED_NUM; ix++) {
        CRGB& out = output[reverse ? reverseIndex(ix) : ix];
        for (int ch = 0; ch < 3; ch++) {
            const uint32_t fine = ((((uint32_t)leds[ix].raw[ch] * useScale) << 8) / 255) + rest[ix][ch];
            out.raw[ch] = (uint8_t)(fine >> 8);
            rest[ix][ch] = (uint8_t)(fine & 0xff);
        }
    }
    FastLED.show();
    currentMa = currentUa / 1000;
    usedScale = useScale;
}
