// Shell commands of the LED chain: "led ..."

#ifndef SHELLLED_HPP
#define SHELLLED_HPP

#include <stdint.h>

#include <FastLED.h>

// LED output set with the "led" command
enum class ShellLed
{
    NORMAL,  // Pipeline status
    COLOUR,  // Test colour
    DEMO,    // Rainbow demo
};

/**
 * @brief Get LED output of the "led" command
 *
 * Test colour for current measurements, or rainbow demo.
 *
 * @param[out] colour  Colour for all LEDs (COLOUR)
 * @param[out] scale   Global brightness (0-255) (COLOUR)
 *
 * @returns the LED output
 */
ShellLed shellLedMode(CRGB& colour, uint8_t& scale);

/**
 * @brief Set LED info for "status"
 *
 * @param[in] currentMa  Estimated LED current [mA]
 * @param[in] scale      Used global brightness (0-255)
 */
void shellLedInfo(const uint32_t currentMa, const uint8_t scale);

/**
 * @brief Run "led" command
 *
 * @param[in] sub   Second word, nullptr if none
 * @param[in] arg1  Third word, nullptr if none
 * @param[in] arg2  Fourth word, nullptr if none
 * @param[in] arg3  Fifth word, nullptr if none
 */
void shellLedCommand(const char* sub, const char* arg1, const char* arg2, const char* arg3);

/**
 * @brief Print help lines of the "led" command
 */
void shellLedHelp();

/**
 * @brief Print LED status line ("status")
 */
void shellLedStatus();

/**
 * @brief Print LED settings ("config")
 */
void shellLedConfig();

#endif // SHELLLED_HPP
