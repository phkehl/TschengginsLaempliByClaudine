// Hello shell commands: LEDs and sound (see shell.cpp for the common commands)
//
//   led <n>                          Light only LED n (0-11 ring, 12 bow)
//   led colour red|green|blue        All LEDs one colour (colour order test)
//   led state <result> <status>      Test state: pass|fail|warn|unknown, stopped|running|waiting|unknown
//   led normal                       Job state from the backend again
//   led brightness [ring|bow <n>]    Show options or set brightness of ring or bow (1-255, saved)
//   led reverse [on|off]             Show or set direction of the light spot (saved)
//   sound                            Trigger the sound module

#include <Arduino.h>
#include <umm_malloc/umm_heap_select.h>

#include "backend.hpp"
#include "config.hpp"
#include "hellosettings.hpp"
#include "ledanim.hpp"
#include "leds.hpp"
#include "shell.hpp"
#include "sound.hpp"

/**
 * @brief Find name of a result or status (ignores case)
 *
 * @returns the enum value, -1 if not found
 */
static int findName(const char* name, const bool status)
{
    for (int ix = 0; (name != nullptr) && (ix < 4); ix++) {
        const char* known = (status ? ledStatusName((LedStatus)ix) : ledResultName((LedResult)ix));
        if (strcasecmp(name, known) == 0) {
            return ix;
        }
    }
    return -1;
}

/**
 * @brief Show or set LED brightness
 *
 * @param[in] which  "ring", "bow" or nullptr (only show)
 * @param[in] value  Brightness (1-255)
 */
static void cmdLedBrightness(const char* which, const char* value)
{
    if (which != nullptr) {
        const long num = shellParseNumber(value);
        const bool ok = ((strcmp(which, "ring") == 0) ? settingsSetRingBrightness(num) :
            ((strcmp(which, "bow") == 0) && settingsSetBowBrightness(num)));
        if (!ok) {
            Serial.println("Usage: led brightness [ring|bow <1-255>]");
            return;
        }
    }
    for (int ix = 0; ix < ledBrightCount(); ix++) {
        const char* name = nullptr;
        const uint8_t option = ledBrightOption(ix, name);
        Serial.printf("  %3u: %s\r\n", option, name);
    }
    shellExtConfig();
}

/**
 * @brief Show or set reverse LEDs
 *
 * @param[in] value  "on", "off" or nullptr (only show)
 */
static void cmdLedReverse(const char* value)
{
    if (value != nullptr) {
        const bool on = (strcmp(value, "on") == 0);
        if ((!on) && (strcmp(value, "off") != 0)) {
            Serial.println("Usage: led reverse [on|off]");
            return;
        }
        settingsSetLedReverse(on ? 1 : 0);
    }
    Serial.printf("Reverse LEDs: %s\r\n", (settingsLedReverse() ? "on" : "off"));
}

/**
 * @brief Run LED command
 */
static void cmdLed(const char* sub, const char* arg1, const char* arg2)
{
    if ((sub != nullptr) && (strcmp(sub, "brightness") == 0)) {
        cmdLedBrightness(arg1, arg2);
        return;
    }
    if ((sub != nullptr) && (strcmp(sub, "reverse") == 0)) {
        cmdLedReverse(arg1);
        return;
    }
    static const char* const colourNames[] = { "red", "green", "blue" };
    static const CRGB colours[] = { CRGB(255, 0, 0), CRGB(0, 255, 0), CRGB(0, 0, 255) };
    const long ix = shellParseNumber(sub);
    if ((ix >= 0) && (ix < LED_PIXELS)) {
        ledsSingle(ix);
        if (ix == LED_BOW_IX) {
            Serial.printf("LED %ld (bow)\r\n", ix);
        } else {
            Serial.printf("LED %ld at %.0f deg\r\n", ix, ledAngle(ix));
        }
        return;
    }
    if ((sub != nullptr) && (strcmp(sub, "colour") == 0)) {
        for (int cx = 0; (arg1 != nullptr) && (cx < 3); cx++) {
            if (strcmp(arg1, colourNames[cx]) == 0) {
                ledsColour(colours[cx]);
                Serial.printf("All LEDs %s\r\n", colourNames[cx]);
                return;
            }
        }
    } else if ((sub != nullptr) && (strcmp(sub, "state") == 0)) {
        const int result = findName(arg1, false);
        const int status = findName(arg2, true);
        if ((result >= 0) && (status >= 0)) {
            ledsTestState((LedResult)result, (LedStatus)status);
            Serial.printf("Test state: %s %s\r\n", ledResultName((LedResult)result),
                ledStatusName((LedStatus)status));
            return;
        }
    } else if ((sub != nullptr) && (strcmp(sub, "normal") == 0)) {
        ledsNormal();
        Serial.println("LEDs: job state from backend");
        return;
    }
    Serial.printf("Usage: led <0-%d> | led colour red|green|blue | led state <result> <status> | led normal | "
        "led brightness [ring|bow <n>] | led reverse [on|off]\r\n", (LED_PIXELS - 1));
}

void shellExtHelp()
{
    Serial.println("  led <n>                          Light only LED n (12: bow)");
    Serial.println("  led colour red|green|blue        All LEDs one colour (test)");
    Serial.println("  led state <result> <status>      Test state, e.g. pass running");
    Serial.println("  led normal                       Job state from backend");
    Serial.println("  led brightness [ring|bow <n>]    Show or set LED brightness");
    Serial.println("  led reverse [on|off]             Show or set spot direction");
    Serial.println("  sound                            Trigger sound module");
}

bool shellExtCommand(const char* cmd, const char* sub, const char* arg1, const char* arg2, const char* arg3)
{
    (void)arg3;
    if (strcmp(cmd, "led") == 0) {
        cmdLed(sub, arg1, arg2);
    } else if (strcmp(cmd, "sound") == 0) {
        soundTrigger();
    } else {
        return false;
    }
    return true;
}

void shellExtStatus()
{
    const LedState state = ledsGetState();
    Serial.printf("Job: %s, status %s, result %s\r\n", (state.used ? "configured" : "not configured"),
        ledStatusName(state.status), ledResultName(state.result));
    Serial.printf("Chip: ESP8266EX, %u MHz, flash %u kB, reset: %s\r\n", ESP.getCpuFreqMHz(),
        (unsigned)(ESP.getFlashChipRealSize() / 1024), ESP.getResetReason().c_str());
#if defined(MMU_IRAM_HEAP)
    HeapSelectIram iram;
    Serial.printf("IRAM heap: %u free (TLS buffers)\r\n", (unsigned)ESP.getFreeHeap());
#endif
}

void shellExtConfig()
{
    Serial.printf("Ring brightness: %u (%s)\r\n", settingsRingBrightness(), ledBrightName(settingsRingBrightness()));
    Serial.printf("Bow brightness: %u (%s)\r\n", settingsBowBrightness(), ledBrightName(settingsBowBrightness()));
    Serial.printf("Reverse LEDs: %s\r\n", (settingsLedReverse() ? "on" : "off"));
}
