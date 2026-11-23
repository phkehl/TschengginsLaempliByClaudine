// LED ring on the cylinder: job state

#ifndef LEDS_HPP
#define LEDS_HPP

#include <stdint.h>

#include <FastLED.h>

#include "backend.hpp"

/**
 * @brief Initialise LEDs (all off)
 */
void ledsInit();

/**
 * @brief Render and show next frame
 *
 * Call often (scheduled function), renders each LED_FRAME_MS.
 */
void ledsUpdate();

/**
 * @brief Get shown job state
 *
 * @returns the test state, or the state from the backend (UNKNOWN before the first state)
 */
LedState ledsGetState();

/**
 * @brief Show a test state instead of the backend state
 *
 * @param[in] result  Result of the last finished run (ring colour)
 * @param[in] status  Job status (ring and bow light)
 */
void ledsTestState(const LedResult result, const LedStatus status);

/**
 * @brief Show the backend state again (end of tests)
 */
void ledsNormal();

/**
 * @brief Light one LED only (test)
 *
 * @param[in] ix  LED index in the chain (0 = first LED of strip A), -1 = back to job state
 */
void ledsSingle(const int ix);

/**
 * @brief Show one colour on all LEDs (test)
 *
 * Stays until ledsNormal().
 *
 * @param[in] colour  Colour (R, G, B as in the code, before LED_ORDER)
 */
void ledsColour(const CRGB colour);

/**
 * @brief Get LED angle
 *
 * @param[in] ix  Ring LED index in the chain (0-11)
 *
 * @returns the angle of the LED on the cylinder [deg]
 */
float ledAngle(const int ix);

#endif // LEDS_HPP
