// Sound module: trigger pulse
//
// As the old firmware (statusFx()): a high pulse of 2 s on GPIO5 plays the sound. Job sounds "fx" play it (see
// jobsound.cpp, soundFxPlay()).

#include <Arduino.h>

#include "config.hpp"
#include "jobsound.hpp"
#include "sound.hpp"

static uint32_t soundStart = 0;
static bool soundOn = false;

void soundInit()
{
    pinMode(SOUND_PIN, OUTPUT);
    digitalWrite(SOUND_PIN, LOW);
}

void soundTrigger()
{
    Serial.printf("Sound: trigger GPIO%u for %lu ms\r\n", SOUND_PIN, (unsigned long)SOUND_PULSE_MS);
    digitalWrite(SOUND_PIN, HIGH);
    soundStart = millis();
    soundOn = true;
}

void soundUpdate()
{
    if (soundOn && ((millis() - soundStart) >= SOUND_PULSE_MS)) {
        digitalWrite(SOUND_PIN, LOW);
        soundOn = false;
    }
}

bool soundFxPlay()
{
    soundTrigger();
    return true;
}
