// Onboard RGB LED: connection and activity

#ifndef BOARDLED_HPP
#define BOARDLED_HPP

#include <stdint.h>

/**
 * @brief Init onboard LED (off)
 */
void boardLedInit();

/**
 * @brief Update onboard LED
 *
 * Call for each LED frame (LED task).
 *
 * @param[in] ms  Time [ms]
 */
void boardLedUpdate(const uint32_t ms);

#endif // BOARDLED_HPP
