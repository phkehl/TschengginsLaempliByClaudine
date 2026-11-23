// Chlotz settings in NVS: display (synced with the backend), LED settings see ledsettings.cpp
//
// NVS namespace "cfg" (as settings.cpp), keys: "dimTo", "dimLvl", "saverTo". Each setter saves at once.
//
// All of them are synced settings: each change calls settingsChanged(). Backend keys: dim_timeout, dim_level,
// saver_timeout, and the LED keys (ledsettings.cpp, see syncsettings.hpp).

#include <Arduino.h>
#include <Preferences.h>

#include "config.hpp"
#include "chlotzsettings.hpp"
#include "ledsettings.hpp"
#include "settings.hpp"
#include "syncsettings.hpp"

static uint16_t dimTimeout = DISP_DIM_TIMEOUT_S;
static uint8_t dimLevel = DISP_DIM_LEVEL;
static uint16_t saverTimeout = DISP_SAVER_S;

/**
 * @brief Save Chlotz settings to NVS
 */
static void saveDisplay()
{
    Preferences prefs;
    prefs.begin("cfg", false);
    prefs.putUShort("dimTo", dimTimeout);
    prefs.putUChar("dimLvl", dimLevel);
    prefs.putUShort("saverTo", saverTimeout);
    prefs.end();
    settingsChanged();
}

void chlotzSettingsInit()
{
    Preferences prefs;
    prefs.begin("cfg", false);
    dimTimeout = prefs.getUShort("dimTo", DISP_DIM_TIMEOUT_S);
    dimLevel = prefs.getUChar("dimLvl", DISP_DIM_LEVEL);
    saverTimeout = prefs.getUShort("saverTo", DISP_SAVER_S);
    prefs.end();
    ledSettingsInit();
}

uint16_t settingsDimTimeout()
{
    return dimTimeout;
}

uint8_t settingsDimLevel()
{
    return dimLevel;
}

bool settingsSetDimTimeout(const long seconds)
{
    if ((seconds < 0) || (seconds > 3600)) {
        return false;
    }
    dimTimeout = seconds;
    saveDisplay();
    return true;
}

bool settingsSetDimLevel(const long percent)
{
    if ((percent < 0) || (percent > 100)) {
        return false;
    }
    dimLevel = percent;
    saveDisplay();
    return true;
}

uint16_t settingsSaverTimeout()
{
    return saverTimeout;
}

bool settingsSetSaverTimeout(const long seconds)
{
    if ((seconds < 0) || (seconds > 3600)) {
        return false;
    }
    saverTimeout = seconds;
    saveDisplay();
    return true;
}

void syncAddSettings(JsonObject obj)
{
    obj["dim_timeout"] = settingsDimTimeout();
    obj["dim_level"] = settingsDimLevel();
    obj["saver_timeout"] = settingsSaverTimeout();
    ledSettingsAdd(obj);
}

bool syncApplySetting(const char* key, JsonVariantConst val)
{
    if (!val.is<long>()) {
        return false;
    }
    const long num = val.as<long>();
    if (strcmp(key, "dim_timeout") == 0) {
        return settingsSetDimTimeout(num);
    }
    if (strcmp(key, "dim_level") == 0) {
        return settingsSetDimLevel(num);
    }
    if (strcmp(key, "saver_timeout") == 0) {
        return settingsSetSaverTimeout(num);
    }
    return ledSettingsApply(key, num);
}

// LED options only (see ledsettings.cpp)
void syncAddOptions(JsonObject obj)
{
    ledSettingsOptions(obj);
}
