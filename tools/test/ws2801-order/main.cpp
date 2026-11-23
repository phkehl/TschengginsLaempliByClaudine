// WS2801 channel order test. Sends the raw bytes, no LED library, so byte 1 is the first byte on the wire.
//
// The test runs in a loop. The serial port (115200 baud) shows each step.
//
// 1. Channel order: all LEDs blink in one byte only. Byte 1 blinks once, byte 2 twice, byte 3 three times. The colours
//    give the order, e.g. 1 blink red, 2 blinks blue, 3 blinks green: RBG.
// 2. Chain order: one LED after the other is white, for 2 s each. There is one more pixel than expected, to see if the
//    top LED is in the chain.
// 3. Onboard LED: the WS2801 LEDs are off, GPIO2 is on for 2 s.

#include <Arduino.h>

constexpr int PIN_CLOCK = 14;
constexpr int PIN_DATA = 13;
constexpr int PIN_ONBOARD = 2;  // low active
constexpr int PIXELS = 4;
constexpr uint8_t LEVEL = 64;
constexpr uint32_t BLINK_MS = 400;
constexpr uint32_t PAUSE_MS = 1500;
constexpr uint32_t LATCH_US = 1000;

/** Send one byte, MSB first */
static void sendByte(const uint8_t value)
{
    for (int bit = 7; bit >= 0; bit--) {
        digitalWrite(PIN_DATA, (((value >> bit) & 1) != 0) ? HIGH : LOW);
        delayMicroseconds(1);
        digitalWrite(PIN_CLOCK, HIGH);
        delayMicroseconds(1);
        digitalWrite(PIN_CLOCK, LOW);
    }
}

/** Set pixels: pixel < 0 means all */
static void sendPixels(const int pixel, const uint8_t byte1, const uint8_t byte2, const uint8_t byte3)
{
    for (int ix = 0; ix < PIXELS; ix++) {
        const bool on = ((pixel < 0) || (pixel == ix));
        sendByte(on ? byte1 : 0);
        sendByte(on ? byte2 : 0);
        sendByte(on ? byte3 : 0);
    }
    delayMicroseconds(LATCH_US);
}

/** Blink all pixels in one byte */
static void blinkByte(const int byteIx)
{
    Serial.printf("Byte %d: %d blink(s)\r\n", byteIx + 1, byteIx + 1);
    for (int count = 0; count <= byteIx; count++) {
        sendPixels(-1, (byteIx == 0) ? LEVEL : 0, (byteIx == 1) ? LEVEL : 0, (byteIx == 2) ? LEVEL : 0);
        delay(BLINK_MS);
        sendPixels(-1, 0, 0, 0);
        delay(BLINK_MS);
    }
    delay(PAUSE_MS);
}

void setup()
{
    Serial.begin(115200);
    pinMode(PIN_CLOCK, OUTPUT);
    pinMode(PIN_DATA, OUTPUT);
    pinMode(PIN_ONBOARD, OUTPUT);
    digitalWrite(PIN_CLOCK, LOW);
    digitalWrite(PIN_ONBOARD, HIGH);
    delay(100);
    sendPixels(-1, 0, 0, 0);
}

void loop()
{
    for (int byteIx = 0; byteIx < 3; byteIx++) {
        blinkByte(byteIx);
    }

    for (int ix = 0; ix < PIXELS; ix++) {
        Serial.printf("Pixel %d: white\r\n", ix + 1);
        sendPixels(ix, LEVEL, LEVEL, LEVEL);
        delay(2000);
    }
    sendPixels(-1, 0, 0, 0);
    delay(PAUSE_MS);

    Serial.printf("Onboard LED GPIO%d: on\r\n", PIN_ONBOARD);
    digitalWrite(PIN_ONBOARD, LOW);
    delay(2000);
    digitalWrite(PIN_ONBOARD, HIGH);
    delay(PAUSE_MS);
}
