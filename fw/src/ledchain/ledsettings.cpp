// LED settings in NVS: animations, brightness, reverse (synced with the backend)
//
// NVS namespace "cfg" (as settings.cpp), keys: "animRun", "animWait", "ledBri", "ledRev". Each setter saves at once.
// The LED task reads them (one byte each, no lock needed).
//
// All of them are synced settings: each change calls settingsChanged(). Backend keys: led_brightness, anim_running,
// anim_waiting, led_reverse (see syncsettings.hpp).

#include <Arduino.h>

#include "compat.hpp"
#include "config.hpp"
#include "ledsettings.hpp"
#include "ledstatus.hpp"
#include "settings.hpp"

static volatile uint8_t animRunning = LED_ANIM_RUNNING;
static volatile uint8_t animWaiting = LED_ANIM_WAITING;
static volatile uint8_t ledBrightness = LED_BRIGHTNESS;
static volatile bool ledReverse = LED_REVERSE;

/**
 * @brief Save LED settings to NVS
 */
static void saveLeds()
{
    Preferences prefs;
    prefs.begin("cfg", false);
    prefs.putUChar("animRun", animRunning);
    prefs.putUChar("animWait", animWaiting);
    prefs.putUChar("ledBri", ledBrightness);
    prefs.putUChar("ledRev", (ledReverse ? 1 : 0));
    prefs.end();
    settingsChanged();
}

void ledSettingsInit()
{
    Preferences prefs;
    prefs.begin("cfg", false);
    animRunning = prefs.getUChar("animRun", LED_ANIM_RUNNING);
    animWaiting = prefs.getUChar("animWait", LED_ANIM_WAITING);
    ledBrightness = max((uint8_t)1, prefs.getUChar("ledBri", LED_BRIGHTNESS));
    ledReverse = (prefs.getUChar("ledRev", (LED_REVERSE ? 1 : 0)) != 0);
    prefs.end();
}

uint8_t settingsAnim(const bool running)
{
    return (running ? animRunning : animWaiting);
}

void settingsSetAnim(const bool running, const uint8_t ix)
{
    if (running) {
        animRunning = ix;
    } else {
        animWaiting = ix;
    }
    saveLeds();
}

uint8_t settingsLedBrightness()
{
    return ledBrightness;
}

bool settingsSetLedBrightness(const long value)
{
    if ((value < 1) || (value > 255)) {
        return false;
    }
    ledBrightness = value;
    saveLeds();
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
    saveLeds();
    return true;
}

void ledSettingsAdd(JsonObject obj)
{
    obj["led_brightness"] = settingsLedBrightness();
    obj["anim_running"] = ledAnimGet(LedStatus::RUNNING);
    obj["anim_waiting"] = ledAnimGet(LedStatus::WAITING);
    obj["led_reverse"] = (settingsLedReverse() ? 1 : 0);
}

bool ledSettingsApply(const char* key, const long num)
{
    if (strcmp(key, "led_brightness") == 0) {
        return settingsSetLedBrightness(num);
    }
    if (strcmp(key, "led_reverse") == 0) {
        return settingsSetLedReverse(num);
    }
    if (strcmp(key, "anim_running") == 0) {
        return ledAnimSet(LedStatus::RUNNING, num);
    }
    if (strcmp(key, "anim_waiting") == 0) {
        return ledAnimSet(LedStatus::WAITING, num);
    }
    return false;
}

// {"led_brightness": [[value, name], ...], "anim_running": [name, ...], "anim_waiting": [name, ...]}
void ledSettingsOptions(JsonObject obj)
{
    JsonArray bright = obj["led_brightness"].to<JsonArray>();
    for (int ix = 0; ix < ledBrightCount(); ix++) {
        const char* name = nullptr;
        JsonArray option = bright.add<JsonArray>();
        option.add(ledBrightOption(ix, name));
        option.add(name);
    }
    const LedStatus statuses[] = { LedStatus::RUNNING, LedStatus::WAITING };
    const char* const keys[] = { "anim_running", "anim_waiting" };
    for (int ix = 0; ix < 2; ix++) {
        JsonArray anims = obj[keys[ix]].to<JsonArray>();
        for (int anim = 0; anim < ledAnimCount(statuses[ix]); anim++) {
            anims.add(ledAnimName(statuses[ix], anim));
        }
    }
}
