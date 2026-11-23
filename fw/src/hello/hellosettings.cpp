// Hello settings in NVS: LED brightness, reverse LEDs (synced with the backend)
//
// NVS namespace "cfg" (as settings.cpp), keys: "ringBri", "bowBri", "ledRev". Each setter saves at once and calls
// settingsChanged(). Backend keys: ring_brightness, bow_brightness, led_reverse, options: the brightness options of
// ledanim.cpp and sound_fx (the sound module, for the job sounds) (see syncsettings.hpp).

#include <Arduino.h>

#include "compat.hpp"
#include "config.hpp"
#include "hellosettings.hpp"
#include "ledanim.hpp"
#include "settings.hpp"
#include "syncsettings.hpp"

static uint8_t ringBrightness = RING_BRIGHTNESS;
static uint8_t bowBrightness = BOW_BRIGHTNESS;
static bool ledReverse = LED_REVERSE;

/**
 * @brief Save hello settings to NVS
 */
static void saveHello()
{
    Preferences prefs;
    prefs.begin("cfg", false);
    prefs.putUChar("ringBri", ringBrightness);
    prefs.putUChar("bowBri", bowBrightness);
    prefs.putUChar("ledRev", (ledReverse ? 1 : 0));
    prefs.end();
    settingsChanged();
}

void helloSettingsInit()
{
    Preferences prefs;
    prefs.begin("cfg", false);
    ringBrightness = max((uint8_t)1, prefs.getUChar("ringBri", RING_BRIGHTNESS));
    bowBrightness = max((uint8_t)1, prefs.getUChar("bowBri", BOW_BRIGHTNESS));
    ledReverse = (prefs.getUChar("ledRev", (LED_REVERSE ? 1 : 0)) != 0);
    prefs.end();
}

uint8_t settingsRingBrightness()
{
    return ringBrightness;
}

bool settingsSetRingBrightness(const long value)
{
    if ((value < 1) || (value > 255)) {
        return false;
    }
    ringBrightness = value;
    saveHello();
    return true;
}

uint8_t settingsBowBrightness()
{
    return bowBrightness;
}

bool settingsSetBowBrightness(const long value)
{
    if ((value < 1) || (value > 255)) {
        return false;
    }
    bowBrightness = value;
    saveHello();
    return true;
}

bool settingsLedReverse()
{
    return ledReverse;
}

bool settingsSetLedReverse(const long value)
{
    if ((value != 0) && (value != 1)) {
        return false;
    }
    ledReverse = (value == 1);
    saveHello();
    return true;
}

void syncAddSettings(JsonObject obj)
{
    obj["ring_brightness"] = settingsRingBrightness();
    obj["bow_brightness"] = settingsBowBrightness();
    obj["led_reverse"] = (settingsLedReverse() ? 1 : 0);
}

bool syncApplySetting(const char* key, JsonVariantConst val)
{
    if (!val.is<long>()) {
        return false;
    }
    if (strcmp(key, "led_reverse") == 0) {
        return settingsSetLedReverse(val.as<long>());
    }
    if (strcmp(key, "ring_brightness") == 0) {
        return settingsSetRingBrightness(val.as<long>());
    }
    if (strcmp(key, "bow_brightness") == 0) {
        return settingsSetBowBrightness(val.as<long>());
    }
    return false;
}

// {"ring_brightness": [[value, name], ...], "bow_brightness": [[value, name], ...]}
void syncAddOptions(JsonObject obj)
{
    for (const char* key : { "ring_brightness", "bow_brightness" }) {
        JsonArray options = obj[key].to<JsonArray>();
        for (int ix = 0; ix < ledBrightCount(); ix++) {
            const char* name = nullptr;
            JsonArray option = options.add<JsonArray>();
            option.add(ledBrightOption(ix, name));
            option.add(name);
        }
    }
    obj["sound_fx"] = true;
}
