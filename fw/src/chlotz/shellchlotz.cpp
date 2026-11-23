// Chlotz shell commands: display, and LEDs (see shellled.cpp; shell.cpp for the common commands)
//
//   display dim <seconds> <percent>  Dim timeout (0 = never) and dim level
//   display saver <seconds>          Screensaver timeout (0 = never)

#include <Arduino.h>

#include "config.hpp"
#include "chlotzsettings.hpp"
#include "shell.hpp"
#include "shellled.hpp"

/**
 * @brief Run display command
 */
static void cmdDisplay(const char* sub, const char* arg1, const char* arg2)
{
    const long timeout = shellParseNumber(arg1);
    const long level = shellParseNumber(arg2);
    if ((sub != nullptr) && (strcmp(sub, "dim") == 0) && (timeout >= 0) && (timeout <= 3600) && (level >= 0) &&
        (level <= 100)) {
        settingsSetDimTimeout(timeout);
        settingsSetDimLevel(level);
        Serial.printf("Display dim: after %ld s, level %ld %%\r\n", timeout, level);
    } else if ((sub != nullptr) && (strcmp(sub, "saver") == 0) && settingsSetSaverTimeout(timeout)) {
        Serial.printf("Screensaver: after %ld s (0 = never)\r\n", timeout);
    } else {
        Serial.println("Usage: display dim <0-3600 s> <0-100 %> | display saver <0-3600 s>");
    }
}

void shellExtHelp()
{
    Serial.println("  display dim <seconds> <percent>  Dim timeout (0 = never), level");
    Serial.println("  display saver <seconds>          Screensaver timeout (0 = never)");
    shellLedHelp();
}

bool shellExtCommand(const char* cmd, const char* sub, const char* arg1, const char* arg2, const char* arg3)
{
    if (strcmp(cmd, "display") == 0) {
        cmdDisplay(sub, arg1, arg2);
    } else if (strcmp(cmd, "led") == 0) {
        shellLedCommand(sub, arg1, arg2, arg3);
    } else {
        return false;
    }
    return true;
}

void shellExtStatus()
{
    shellLedStatus();
}

void shellExtConfig()
{
    Serial.printf("Display dim: after %u s (0 = never), level %u %%\r\n", settingsDimTimeout(), settingsDimLevel());
    Serial.printf("Screensaver: after %u s (0 = never)\r\n", settingsSaverTimeout());
    shellLedConfig();
}
