// LED animations and colours of job states

#ifndef LEDANIM_HPP
#define LEDANIM_HPP

#include <stdint.h>

#include <FastLED.h>

#include "backend.hpp"

/**
 * @brief Get colour of a result
 *
 * @param[in] result  Result of the last finished run of the job
 *
 * @returns the colour (full brightness)
 */
CRGB ledStatusColour(const LedResult result);

/**
 * @brief Get number of animations of a status (RUNNING and WAITING: the same list)
 *
 * @param[in] status  RUNNING or WAITING (others: 0)
 *
 * @returns the number of animations
 */
int ledAnimCount(const LedStatus status);

/**
 * @brief Get animation name
 *
 * @param[in] status  RUNNING or WAITING
 * @param[in] ix      Animation index
 *
 * @returns the name, "" if no such animation
 */
const char* ledAnimName(const LedStatus status, const int ix);

/**
 * @brief Get perceived level of a status at a time
 *
 * @param[in] status  Job status: STOPPED 1, UNKNOWN blink, RUNNING and WAITING animation
 * @param[in] ix      Animation index (RUNNING, WAITING), invalid gives 1
 * @param[in] ms      Time [ms]
 *
 * @returns the perceived level (0-1)
 */
float ledAnimLevel(const LedStatus status, const int ix, const uint32_t ms);

/**
 * @brief Get LED value of a level
 *
 * @param[in] level  Perceived level (0-1)
 *
 * @returns the value (0-255, level ^ LED_GAMMA) to scale a colour with
 */
uint8_t ledLevelValue(const float level);

/**
 * @brief Get number of LED brightness options
 */
int ledBrightCount();

/**
 * @brief Get LED brightness option
 *
 * @param[in]  ix    Option index
 * @param[out] name  Option name, e.g. "dim"
 *
 * @returns the brightness (1-255), 0 if no such option
 */
uint8_t ledBrightOption(const int ix, const char*& name);

/**
 * @brief Get name of a brightness value
 *
 * @param[in] value  Brightness (1-255)
 *
 * @returns the option name, "custom" if not an option
 */
const char* ledBrightName(const uint8_t value);

/**
 * @brief Render noise: a random colour on each LED
 *
 * Same colours for NOISE_MS, then new ones (no state, a hash of time and LED index).
 *
 * @param[out] leds  LEDs
 * @param[in]  num   Number of LEDs
 * @param[in]  ms    Time [ms]
 */
void ledNoiseRender(CRGB* leds, const int num, const uint32_t ms);

#endif // LEDANIM_HPP
