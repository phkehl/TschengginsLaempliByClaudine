// Shell commands of the LED chain (see shell.cpp for the common commands)
//
//   led <r> <g> <b> [scale]          All LEDs one colour (test, e.g. current measurement)
//   led off                          All LEDs off (test)
//   led demo                         Rainbow demo
//   led normal                       Normal output (job status)
//   led anim                         Show LED animations of RUNNING and WAITING
//   led anim running|waiting <n>     Use animation n (saved)
//   led brightness [<value>]         Show options or set LED brightness (1-255, saved)
//   led reverse [on|off]             Show or set reverse LEDs (saved)

#include <Arduino.h>

#include "compat.hpp"
#include "config.hpp"
#include "ledsettings.hpp"
#include "ledstatus.hpp"
#include "shell.hpp"
#include "shellled.hpp"

static Atomic<uint32_t> infoCurrentMa(0);
static Atomic<uint8_t> infoScale(0);

// LED test colour (for current measurements) or demo
static ShellLed ledMode = ShellLed::NORMAL;
static CRGB ledTestColour = CRGB::Black;
static uint8_t ledTestScale = 255;

ShellLed shellLedMode(CRGB& colour, uint8_t& scale)
{
    colour = ledTestColour;
    scale = ledTestScale;
    return ledMode;
}

/**
 * @brief Show or set LED animations
 *
 * @param[in] which  "running", "waiting" or nullptr (only show)
 * @param[in] num    Animation index
 */
static void cmdLedAnim(const char* which, const char* num)
{
    const LedStatus statuses[] = { LedStatus::RUNNING, LedStatus::WAITING };
    if (which != nullptr) {
        const bool running = (strcmp(which, "running") == 0);
        const long ix = shellParseNumber(num);
        if ((!running && (strcmp(which, "waiting") != 0)) ||
            !ledAnimSet((running ? LedStatus::RUNNING : LedStatus::WAITING), (int)ix)) {
            Serial.println("Usage: led anim [running|waiting <n>]");
            return;
        }
    }
    for (const LedStatus status : statuses) {
        Serial.printf("%s:\r\n", ((status == LedStatus::RUNNING) ? "running" : "waiting"));
        for (int ix = 0; ix < ledAnimCount(status); ix++) {
            Serial.printf("  %c %d: %s\r\n", ((ix == ledAnimGet(status)) ? '*' : ' '), ix,
                ledAnimName(status, ix));
        }
    }
}

/**
 * @brief Show or set LED brightness
 *
 * @param[in] value  Brightness (1-255) or nullptr (only show)
 */
static void cmdLedBrightness(const char* value)
{
    if ((value != nullptr) && !settingsSetLedBrightness(shellParseNumber(value))) {
        Serial.println("Usage: led brightness [1-255]");
        return;
    }
    for (int ix = 0; ix < ledBrightCount(); ix++) {
        const char* name = nullptr;
        const uint8_t option = ledBrightOption(ix, name);
        Serial.printf("  %c %3u: %s\r\n", ((option == settingsLedBrightness()) ? '*' : ' '), option, name);
    }
    Serial.printf("LED brightness: %u (%s)\r\n", settingsLedBrightness(), ledBrightName(settingsLedBrightness()));
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

void shellLedCommand(const char* sub, const char* arg1, const char* arg2, const char* arg3)
{
    if ((sub != nullptr) && (strcmp(sub, "anim") == 0)) {
        cmdLedAnim(arg1, arg2);
        return;
    }
    if ((sub != nullptr) && (strcmp(sub, "brightness") == 0)) {
        cmdLedBrightness(arg1);
        return;
    }
    if ((sub != nullptr) && (strcmp(sub, "reverse") == 0)) {
        cmdLedReverse(arg1);
        return;
    }
    if ((sub != nullptr) && (strcmp(sub, "normal") == 0)) {
        ledMode = ShellLed::NORMAL;
        Serial.println("LEDs: normal output");
        return;
    }
    if ((sub != nullptr) && (strcmp(sub, "demo") == 0)) {
        ledMode = ShellLed::DEMO;
        Serial.println("LEDs: rainbow demo");
        return;
    }
    long val[4] = { 0, 0, 0, 255 };
    bool ok = (sub != nullptr);
    if (ok && (strcmp(sub, "off") != 0)) {
        const char* args[4] = { sub, arg1, arg2, arg3 };
        for (int ix = 0; ix < 4; ix++) {
            if ((ix == 3) && (args[ix] == nullptr)) {
                break;
            }
            val[ix] = shellParseNumber(args[ix]);
            if ((val[ix] < 0) || (val[ix] > 255)) {
                ok = false;
            }
        }
    }
    if (!ok) {
        Serial.println("Usage: led <r> <g> <b> [scale] | led off | led demo | led normal");
        return;
    }
    ledTestColour = CRGB(val[0], val[1], val[2]);
    ledTestScale = val[3];
    ledMode = ShellLed::COLOUR;
    Serial.printf("LEDs: test colour %ld %ld %ld, scale %ld\r\n", val[0], val[1], val[2], val[3]);
}

void shellLedInfo(const uint32_t currentMa, const uint8_t scale)
{
    infoCurrentMa = currentMa;
    infoScale = scale;
}

void shellLedHelp()
{
    Serial.println("  led <r> <g> <b> [scale]          All LEDs one colour (test, scale 255)");
    Serial.println("  led off | demo | normal          LEDs off, rainbow, job status");
    Serial.println("  led anim [running|waiting <n>]   Show or set LED animations");
    Serial.println("  led brightness [<value>]         Show or set LED brightness");
    Serial.println("  led reverse [on|off]             Show or set reverse LEDs");
}

void shellLedStatus()
{
    Serial.printf("LEDs: %d, current %u mA (max %u), scale %u/%u\r\n", LED_NUM, (unsigned)infoCurrentMa.load(),
        (unsigned)LED_MAX_MA, (unsigned)infoScale.load(), (unsigned)settingsLedBrightness());
}

void shellLedConfig()
{
    Serial.printf("LED brightness: %u (%s)\r\n", settingsLedBrightness(), ledBrightName(settingsLedBrightness()));
    Serial.printf("Reverse LEDs: %s\r\n", (settingsLedReverse() ? "on" : "off"));
    Serial.printf("LED animation: running %d (%s), waiting %d (%s)\r\n", ledAnimGet(LedStatus::RUNNING),
        ledAnimName(LedStatus::RUNNING, ledAnimGet(LedStatus::RUNNING)), ledAnimGet(LedStatus::WAITING),
        ledAnimName(LedStatus::WAITING, ledAnimGet(LedStatus::WAITING)));
}
