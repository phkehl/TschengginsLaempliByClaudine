// LED output: WS2801 chain with current limit and dithering

#ifndef LEDOUT_HPP
#define LEDOUT_HPP

#include <stdint.h>

#include <FastLED.h>

/**
 * @brief Init WS2801 LED chain
 *
 * Pins, number of LEDs and colour order from config.hpp.
 */
void ledOutInit();

/**
 * @brief Show LEDs within current limit
 *
 * @param[in]  leds       Colours (full scale) of the LED_NUM LEDs, in chain order
 * @param[in]  scale      Wanted global brightness (0-255)
 * @param[out] currentMa  Estimated current [mA]
 * @param[out] usedScale  Used global brightness (0-255)
 */
void ledOutShow(const CRGB* leds, const uint8_t scale, uint32_t& currentMa, uint8_t& usedScale);

#endif // LEDOUT_HPP
