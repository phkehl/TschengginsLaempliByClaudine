// LED test from the web page ("led" event of the backend)

#ifndef LEDREMOTE_HPP
#define LEDREMOTE_HPP

#include <stdint.h>

#include <ArduinoJson.h>
#include <FastLED.h>

static constexpr uint32_t LED_REMOTE_MS = 60000;  // A test ends after this time
static constexpr uint32_t LED_FIND_MS = 600;      // Blink period of "find"

// LED test mode
enum class LedRemote
{
    NONE,    // Normal output (job state)
    COLOUR,  // All LEDs one colour (also black: "off")
    DEMO,    // Rainbow demo
    FIND,    // Some LEDs blink white, the others are off
};

// LED test
struct LedRemoteTest
{
    LedRemote mode = LedRemote::NONE;
    CRGB colour = CRGB::Black;  // COLOUR
    int first = 0;              // FIND: first job LED (0 = LED 1)
    int count = 0;              // FIND: number of LEDs
};

/**
 * @brief Set LED test from a "led" event
 *
 * @param[in] data  {"mode": "red|green|blue|white|off|demo|find|normal", "first": n, "count": n} (first and count for
 *                  find)
 *
 * @returns false for bad data (the test stays as it is)
 */
bool ledRemoteSet(JsonObjectConst data);

/**
 * @brief Get LED test (NONE after LED_REMOTE_MS)
 *
 * @returns the test
 */
LedRemoteTest ledRemoteGet();

/**
 * @brief Check if a find LED is on now (blink)
 *
 * @param[in] test  The test
 * @param[in] ix    Job LED index (0 = LED 1)
 *
 * @returns true if the LED is on
 */
bool ledRemoteFindOn(const LedRemoteTest& test, const int ix);

/**
 * @brief Render COLOUR or FIND (DEMO: each firmware has its own rainbow)
 *
 * @param[out] leds  Colours (full scale), job LED order
 * @param[in]  num   Number of LEDs
 * @param[in]  test  The test
 *
 * @returns false for other modes (leds unchanged)
 */
bool ledRemoteRender(CRGB* leds, const int num, const LedRemoteTest& test);

/**
 * @brief Get change counter (set and end of a test: report at once)
 *
 * @returns the counter
 */
uint32_t ledRemoteRev();

/**
 * @brief Add LED test to the report
 *
 * @param[out] obj  {"mode": ..., "first": n, "count": n}
 *
 * @returns false if no test runs (obj stays empty)
 */
bool ledRemoteReport(JsonObject obj);

/**
 * @brief Add LED test option (for the web page)
 *
 * @param[out] obj  Options: "led_test": true
 */
void ledRemoteOptions(JsonObject obj);

#endif // LEDREMOTE_HPP
