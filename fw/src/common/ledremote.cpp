// LED test from the web page ("led" event of the backend)
//
// The buttons on the device page send a mode: red, green, blue, white, off (all LEDs one colour), demo (rainbow), find
// (some LEDs blink white, the others are off: find an LED or a row) and normal (end of the test). A test ends by itself
// after LED_REMOTE_MS, so a closed web page does not leave the LEDs in a test. Each firmware renders the test in its
// LED frame (ledRemoteGet()), at its normal brightness. The report has the test ("led_test"), the first report the
// option "led_test": true (the page shows the buttons only then).

#include <Arduino.h>

#include "compat.hpp"
#include "ledremote.hpp"

// Modes with a colour, then the others
struct ModeName
{
    const char* name;
    LedRemote mode;
    CRGB colour;
};
static const ModeName MODES[] = {
    { "red", LedRemote::COLOUR, CRGB(255, 0, 0) },
    { "green", LedRemote::COLOUR, CRGB(0, 255, 0) },
    { "blue", LedRemote::COLOUR, CRGB(0, 0, 255) },
    { "white", LedRemote::COLOUR, CRGB(255, 255, 255) },
    { "off", LedRemote::COLOUR, CRGB(0, 0, 0) },
    { "demo", LedRemote::DEMO, CRGB(0, 0, 0) },
    { "find", LedRemote::FIND, CRGB(0, 0, 0) },
    { "normal", LedRemote::NONE, CRGB(0, 0, 0) },
};
static constexpr int MODES_NUM = (int)(sizeof(MODES) / sizeof(MODES[0]));

static Mutex mtx;
static LedRemoteTest current;
static const char* currentName = "";
static uint32_t startMs = 0;
static Atomic<uint32_t> rev(0);

bool ledRemoteSet(JsonObjectConst data)
{
    const char* name = data["mode"] | "";
    const ModeName* found = nullptr;
    for (int ix = 0; ix < MODES_NUM; ix++) {
        if (strcmp(name, MODES[ix].name) == 0) {
            found = &MODES[ix];
        }
    }
    const int first = data["first"] | -1;
    const int count = data["count"] | 0;
    if ((found == nullptr) || ((found->mode == LedRemote::FIND) && ((first < 0) || (count < 1)))) {
        return false;
    }
    {
        Lock lock(mtx);
        current.mode = found->mode;
        current.colour = found->colour;
        current.first = first;
        current.count = count;
        currentName = found->name;
        startMs = millis();
    }
    rev++;
    return true;
}

LedRemoteTest ledRemoteGet()
{
    bool ended = false;
    LedRemoteTest test;
    {
        Lock lock(mtx);
        if ((current.mode != LedRemote::NONE) && ((millis() - startMs) >= LED_REMOTE_MS)) {
            current.mode = LedRemote::NONE;
            ended = true;
        }
        test = current;
    }
    if (ended) {
        rev++;
    }
    return test;
}

bool ledRemoteFindOn(const LedRemoteTest& test, const int ix)
{
    return ((ix >= test.first) && (ix < (test.first + test.count)) &&
            ((millis() % LED_FIND_MS) < ((LED_FIND_MS * 2) / 3)));
}

bool ledRemoteRender(CRGB* leds, const int num, const LedRemoteTest& test)
{
    if (test.mode == LedRemote::COLOUR) {
        fill_solid(leds, num, test.colour);
        return true;
    }
    if (test.mode == LedRemote::FIND) {
        for (int ix = 0; ix < num; ix++) {
            leds[ix] = (ledRemoteFindOn(test, ix) ? CRGB(255, 255, 255) : CRGB(0, 0, 0));
        }
        return true;
    }
    return false;
}

uint32_t ledRemoteRev()
{
    return rev.load();
}

bool ledRemoteReport(JsonObject obj)
{
    const LedRemoteTest test = ledRemoteGet();
    if (test.mode == LedRemote::NONE) {
        return false;
    }
    Lock lock(mtx);
    obj["mode"] = currentName;
    if (test.mode == LedRemote::FIND) {
        obj["first"] = test.first;
        obj["count"] = test.count;
    }
    return true;
}

void ledRemoteOptions(JsonObject obj)
{
    obj["led_test"] = true;
}
