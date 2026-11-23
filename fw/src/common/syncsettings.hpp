// Synced settings of a firmware: implemented by each firmware
//
// The backend syncs the device name (see backend.cpp) and the settings of the firmware (e.g. display settings of the
// Chlotz). The keys must be in backend/laempli/settings.py. A change on the device calls settingsChanged().

#ifndef SYNCSETTINGS_HPP
#define SYNCSETTINGS_HPP

#include <ArduinoJson.h>

/**
 * @brief Add synced settings of the firmware
 *
 * @param[out] obj  Settings {key: value}
 */
void syncAddSettings(JsonObject obj);

/**
 * @brief Apply one setting from the backend
 *
 * @param[in] key  Setting key
 * @param[in] val  Value
 *
 * @returns true if set, false if unknown key or bad value
 */
bool syncApplySetting(const char* key, JsonVariantConst val);

/**
 * @brief Add setting options of the firmware (for the web page)
 *
 * @param[out] obj  Options {key: [...]}
 */
void syncAddOptions(JsonObject obj);

#endif // SYNCSETTINGS_HPP
