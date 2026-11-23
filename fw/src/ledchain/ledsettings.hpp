// LED settings in NVS: animations, brightness, reverse (synced with the backend)

#ifndef LEDSETTINGS_HPP
#define LEDSETTINGS_HPP

#include <stdint.h>

#include <ArduinoJson.h>

/**
 * @brief Load LED settings from NVS
 */
void ledSettingsInit();

/**
 * @brief Get LED animation of a job status
 *
 * @param[in] running  true: RUNNING, false: WAITING
 *
 * @returns the animation index (see ledanim.cpp)
 */
uint8_t settingsAnim(const bool running);

/**
 * @brief Set LED animation of a job status (the caller checks the index)
 *
 * @param[in] running  true: RUNNING, false: WAITING
 * @param[in] ix       Animation index
 */
void settingsSetAnim(const bool running, const uint8_t ix);

/**
 * @brief Get global LED brightness
 *
 * @returns the brightness (1-255), default LED_BRIGHTNESS
 */
uint8_t settingsLedBrightness();

/**
 * @brief Set global LED brightness
 *
 * @param[in] value  Brightness (1-255)
 *
 * @returns true on success, false if invalid
 */
bool settingsSetLedBrightness(const long value);

/**
 * @brief Get reverse LEDs setting
 *
 * The reverse order is in ledout.cpp (LED_REVERSE_LEN). On the Chlotz it also flips the display.
 *
 * @returns true if reversed, default LED_REVERSE
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

/**
 * @brief Add synced LED settings
 *
 * For syncAddSettings() of the firmware.
 *
 * @param[out] obj  Settings {key: value}
 */
void ledSettingsAdd(JsonObject obj);

/**
 * @brief Apply one synced LED setting
 *
 * For syncApplySetting() of the firmware.
 *
 * @param[in] key  Setting key
 * @param[in] num  Value
 *
 * @returns true if set, false if unknown key or bad value
 */
bool ledSettingsApply(const char* key, const long num);

/**
 * @brief Add LED setting options
 *
 * For syncAddOptions() of the firmware.
 *
 * @param[out] obj  Options {key: [...]}
 */
void ledSettingsOptions(JsonObject obj);

#endif // LEDSETTINGS_HPP
