// Hello settings in NVS: LED brightness, reverse LEDs (synced with the backend)

#ifndef HELLOSETTINGS_HPP
#define HELLOSETTINGS_HPP

#include <stdint.h>

/**
 * @brief Load hello settings from NVS
 */
void helloSettingsInit();

/**
 * @brief Get ring brightness
 *
 * @returns the brightness (1-255), default RING_BRIGHTNESS
 */
uint8_t settingsRingBrightness();

/**
 * @brief Set ring brightness
 *
 * @param[in] value  Brightness (1-255)
 *
 * @returns true on success, false if invalid
 */
bool settingsSetRingBrightness(const long value);

/**
 * @brief Get bow brightness
 *
 * @returns the brightness (1-255), default BOW_BRIGHTNESS
 */
uint8_t settingsBowBrightness();

/**
 * @brief Set bow brightness
 *
 * @param[in] value  Brightness (1-255)
 *
 * @returns true on success, false if invalid
 */
bool settingsSetBowBrightness(const long value);

/**
 * @brief Get reverse LEDs setting
 *
 * @returns true if the light spot goes the other way, default LED_REVERSE
 */
bool settingsLedReverse();

/**
 * @brief Set reverse LEDs setting
 *
 * @param[in] value  0: off, 1: on
 *
 * @returns true on success, false if invalid
 */
bool settingsSetLedReverse(const long value);

#endif // HELLOSETTINGS_HPP
