// Tschenggins Lämpli "hello" on the Wemos D1 mini (ESP8266EX): one job on an LED ring with a bow LED, sound module
//
// The Arduino loop runs WiFi, backend client and shell. The LEDs run as scheduled function each LED_FRAME_MS
// (leds.cpp): the ESP8266 core runs it after each loop and at each yield() and delay(), so the animation continues
// while the backend client waits for the network. TLS handshakes do not yield (about 1 s, the LEDs stop).
//
// Onboard LED: blinks until the backend is connected, then off.
//
// Piezo speaker: melodies (melody.cpp, scheduled function as the LEDs), shell command "melody", boot sound (Morse
// code BOOT_MORSE).
//
// Firmware update: with the button on the web page (update event, backend.cpp) or the shell command "ota install".
// There is no rollback on the ESP8266: a firmware that does not start needs the USB cable.
//
// Synced settings: device name, ring and bow brightness, reverse LEDs (hellosettings.cpp).

#include <Arduino.h>
#include <Schedule.h>

#include "backend.hpp"
#include "config.hpp"
#include "hellosettings.hpp"
#include "leds.hpp"
#include "melody.hpp"
#include "settings.hpp"
#include "shell.hpp"
#include "sound.hpp"
#include "wifi.hpp"

void setup()
{
    Serial.begin(115200);
    pinMode(BOARD_LED_PIN, OUTPUT);
    digitalWrite(BOARD_LED_PIN, HIGH);
    soundInit();
    melodyInit();
    melodyMorse(BOOT_MORSE);
    helloSettingsInit();
    ledsInit();
    schedule_recurrent_function_us([]() {
        ledsUpdate();
        return true;
    }, LED_FRAME_MS * 1000);

    Serial.print("\r\n");
    settingsInit();
    wifiInit();
    backendInit();
    shellInit();
}

/**
 * @brief Update onboard LED
 */
static void boardLedUpdate()
{
    const bool connected = (backendGetStatus().state == BackendState::CONNECTED);
    const bool on = (!connected && ((millis() % BOARD_LED_BLINK_MS) < (BOARD_LED_BLINK_MS / 2)));
    digitalWrite(BOARD_LED_PIN, (on ? LOW : HIGH));
}

void loop()
{
    wifiUpdate();
    backendUpdate();
    shellUpdate();
    soundUpdate();
    boardLedUpdate();
    delay(5);
}
