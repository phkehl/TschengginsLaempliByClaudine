// LED status: job state as colour and light

#ifndef LEDSTATUS_HPP
#define LEDSTATUS_HPP

#include <FastLED.h>

#include "backend.hpp"
#include "ledanim.hpp"

/**
 * @brief Make LED colours from the backend state
 *
 * Call for each LED frame. Steady, animated or blinking colour of each used LED, unused LEDs off. Before the first
 * state from the backend, all LEDs blink in the UNKNOWN colour.
 *
 * @param[out] leds  LED colours
 * @param[in]  num   Number of LEDs
 */
void ledStatusRender(CRGB* leds, const int num);

/**
 * @brief Get selected animation of a status
 *
 * @param[in] status  RUNNING or WAITING
 *
 * @returns the animation index
 */
int ledAnimGet(const LedStatus status);

/**
 * @brief Select animation of a status (saved in the settings)
 *
 * @param[in] status  RUNNING or WAITING
 * @param[in] ix      Animation index
 *
 * @returns true if OK, false if no such animation
 */
bool ledAnimSet(const LedStatus status, const int ix);

/**
 * @brief Get LED value of a status at a time
 *
 * @param[in] status  Job status: STOPPED 255, UNKNOWN blink, RUNNING and WAITING animation
 * @param[in] ix      Animation index, -1 for the selected one
 * @param[in] ms      Time [ms]
 *
 * @returns the value (0-255, gamma applied) to scale the colour with
 */
uint8_t ledAnimValue(const LedStatus status, const int ix, const uint32_t ms);

#endif // LEDSTATUS_HPP
