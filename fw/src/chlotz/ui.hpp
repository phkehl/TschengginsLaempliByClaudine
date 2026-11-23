// User interface: screens and touch

#ifndef UI_HPP
#define UI_HPP

#include <stdint.h>

#include <FastLED.h>

/**
 * @brief Run user interface
 *
 * Call often (every 20 ms or faster). Handles touch input and draws the active screen.
 *
 * @param[in] leds       LED colours (before brightness scale)
 * @param[in] num        Number of LEDs
 * @param[in] currentMa  Estimated LED current [mA]
 * @param[in] scale      Used global brightness (0-255)
 */
void uiUpdate(const CRGB* leds, const int num, const uint32_t currentMa, const uint8_t scale);

#endif // UI_HPP
