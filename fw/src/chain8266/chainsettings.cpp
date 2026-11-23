// Chlötzli and Chischtli synced settings: only the LED settings (see ledsettings.cpp, syncsettings.hpp)

#include <Arduino.h>

#include "ledsettings.hpp"
#include "syncsettings.hpp"

void syncAddSettings(JsonObject obj)
{
    ledSettingsAdd(obj);
}

bool syncApplySetting(const char* key, JsonVariantConst val)
{
    if (!val.is<long>()) {
        return false;
    }
    return ledSettingsApply(key, val.as<long>());
}

void syncAddOptions(JsonObject obj)
{
    ledSettingsOptions(obj);
}
