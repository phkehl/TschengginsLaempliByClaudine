// Chlotz settings in NVS: display (synced with the backend)

#ifndef CHLOTZSETTINGS_HPP
#define CHLOTZSETTINGS_HPP

#include <stdint.h>

#include "ledsettings.hpp"

/**
 * @brief Load Chlotz settings from NVS
 *
 * Also the LED settings (ledSettingsInit()).
 */
void chlotzSettingsInit();

/**
 * @brief Get dim timeout
 *
 * @returns the timeout [s], 0 = never dim
 */
uint16_t settingsDimTimeout();

/**
 * @brief Get dim level
 *
 * @returns the level [%] of full brightness
 */
uint8_t settingsDimLevel();

/**
 * @brief Set dim timeout
 *
 * @param[in] seconds  Timeout (0-3600 s), 0 = never dim
 *
 * @returns true on success, false if invalid
 */
bool settingsSetDimTimeout(const long seconds);

/**
 * @brief Set dim level
 *
 * @param[in] percent  Level (0-100 %)
 *
 * @returns true on success, false if invalid
 */
bool settingsSetDimLevel(const long percent);

/**
 * @brief Get screensaver timeout
 *
 * @returns the timeout [s], 0 = no screensaver
 */
uint16_t settingsSaverTimeout();

/**
 * @brief Set screensaver timeout
 *
 * @param[in] seconds  Timeout (0-3600 s), 0 = no screensaver
 *
 * @returns true on success, false if invalid
 */
bool settingsSetSaverTimeout(const long seconds);

#endif // CHLOTZSETTINGS_HPP
