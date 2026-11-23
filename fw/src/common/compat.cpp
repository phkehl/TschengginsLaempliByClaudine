// ESP32 and ESP8266 differences for the common code: yield()
//
// FastLED (3.9.4, FastLED.cpp) has an empty weak yield(). The yield() of the Arduino cores is a weak alias of
// __yield(), so the linker can take the empty one. Then optimistic_yield() does nothing on the ESP8266: the BearSSL
// handshake loop runs without a yield and the soft watchdog resets the chip. This strong yield() calls the core.

#include <Arduino.h>

extern "C" void __yield();

extern "C" void yield()
{
    __yield();
}
