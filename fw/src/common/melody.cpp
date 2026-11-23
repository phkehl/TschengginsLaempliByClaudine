// Melody player: built-in RTTTL melodies on the speaker
//
// The speaker gets a square wave: SPEAKER_DUTY [%] high, the rest low (50 = loudest). Lower values are quieter (the
// level of the fundamental is about sin(pi * duty)).
//
// - ESP32: LEDC channel SPEAKER_CHANNEL (its timer must not be used by other channels). A task (core 1, above the
//   Arduino loop) runs the player each PLAY_TICK_MS, so a slow display update does not change the rhythm.
// - ESP8266: waveform generator (as tone()). The player runs as scheduled function each PLAY_TICK_MS (after each
//   loop, at yield() and delay()). TLS handshakes do not yield: then the note is longer.
//
// Each note plays for its time minus a short gap, so that repeated notes are heard as separate notes.
//
// Morse code (melodyMorse(), e.g. the boot sound BOOT_MORSE): the same player, with elements instead of notes. Unit
// MORSE_UNIT_MS (60 ms: about 20 words per minute): dot 1 unit, dash 3, gap in a character 1, between characters 3,
// between words 7. "FFI" has 27 units, about 1.6 s.
//
// No speaker (SPEAKER_PIN -1, e.g. Stägge): no player, melodyPlay() fails.

#include <Arduino.h>

#if defined(ESP8266)
#include <Schedule.h>
#include <core_esp8266_waveform.h>
#endif

#include "compat.hpp"
#include "config.hpp"
#include "melody.hpp"
#include "rtttl.hpp"

static constexpr uint32_t PLAY_TICK_MS = 2;  // Player update period
static constexpr uint32_t GAP_MIN_MS = 5;    // Gap after each note: min.
static constexpr uint32_t GAP_MAX_MS = 25;   // Gap after each note: max.
static constexpr uint16_t FREQ_MIN = 100;    // Lowest tone [Hz]
static constexpr uint16_t FREQ_MAX = 10000;  // Highest tone [Hz]
static constexpr uint32_t LATE_MS = 50;      // Player late: next note now
static constexpr bool HAVE_SPEAKER = (SPEAKER_PIN >= 0);
static constexpr uint32_t MORSE_UNIT_MS = 60;  // Morse code unit (dot)
static constexpr uint16_t MORSE_FREQ = 1000;    // Morse code tone [Hz]
static constexpr int MORSE_MAX = 128;            // Max. elements
static constexpr int PLAYING_MORSE = -2;         // "playing" for Morse code

// Morse code of A-Z, then 0-9
static const char* const MORSE_CODES[36] = { ".-", "-...", "-.-.", "-..", ".", "..-.", "--.", "....", "..", ".---",
    "-.-", ".-..", "--", "-.", "---", ".--.", "--.-", ".-.", "...", "-", "..-", "...-", ".--", "-..-", "-.--",
    "--..", "-----", ".----", "..---", "...--", "....-", ".....", "-....", "--...", "---..", "----." };

static Mutex mutex;
static Rtttl parser;
static int playing = -1;      // Melody index, -1: none, PLAYING_MORSE
static int8_t morse[MORSE_MAX];  // Morse elements [units]: > 0 tone, < 0 silence
static int morseNum = 0;
static int morseIx = 0;
static bool toneOn = false;   // Note sounds (not in the gap)
static uint32_t noteStart = 0;
static uint32_t toneMs = 0;   // Note sounds for this time
static uint32_t noteMs = 0;   // Note time (with gap)

/**
 * @brief Output a tone
 *
 * @param[in] freq  Frequency [Hz], 0 = off
 */
static void speakerTone(const uint16_t freq)
{
#if defined(ESP8266)
    if (freq == 0) {
        stopWaveform(SPEAKER_PIN);
        digitalWrite(SPEAKER_PIN, LOW);
    } else {
        const uint32_t periodUs = 1000000 / freq;
        const uint32_t highUs = (periodUs * SPEAKER_DUTY) / 100;
        startWaveform(SPEAKER_PIN, highUs, periodUs - highUs, 0);
    }
#else
    if (freq == 0) {
        ledcWrite(SPEAKER_CHANNEL, 0);
    } else {
        // 10 bit duty
        ledcSetup(SPEAKER_CHANNEL, freq, 10);
        ledcWrite(SPEAKER_CHANNEL, (1024 * SPEAKER_DUTY) / 100);
    }
#endif
}

/**
 * @brief Start the next note, or end the melody
 *
 * @param[in] start  Start time of the note
 */
static void startNote(const uint32_t start)
{
    if (playing == PLAYING_MORSE) {
        if (morseIx >= morseNum) {
            speakerTone(0);
            toneOn = false;
            playing = -1;
            return;
        }
        const int8_t units = morse[morseIx++];
        noteStart = start;
        noteMs = (uint32_t)abs(units) * MORSE_UNIT_MS;
        toneOn = (units > 0);
        toneMs = (toneOn ? noteMs : 0);
        speakerTone(toneOn ? MORSE_FREQ : 0);
        return;
    }
    RtttlNote note;
    if (!parser.next(note)) {
        speakerTone(0);
        toneOn = false;
        playing = -1;
        return;
    }
    noteStart = start;
    noteMs = note.ms;
    const uint32_t gap = constrain(noteMs / 10, GAP_MIN_MS, GAP_MAX_MS);
    toneMs = ((noteMs > gap) ? (noteMs - gap) : 0);
    toneOn = ((note.freq != 0) && (toneMs > 0));
    speakerTone(toneOn ? constrain(note.freq, FREQ_MIN, FREQ_MAX) : 0);
}

/**
 * @brief Run the player: end of tone, next note
 */
static void playerUpdate()
{
    Lock lock(mutex);
    if (playing == -1) {
        return;
    }
    const uint32_t now = millis();
    if (toneOn && ((now - noteStart) >= toneMs)) {
        speakerTone(0);
        toneOn = false;
    }
    if ((now - noteStart) >= noteMs) {
        // From the planned time (no drift), but
        // not when late (blocked loop): no rush
        const uint32_t next = noteStart + noteMs;
        startNote(((now - next) > LATE_MS) ? now : next);
    }
}

#if !defined(ESP8266)
/**
 * @brief Player task
 */
static void playerTask(void* arg)
{
    (void)arg;
    while (true) {
        playerUpdate();
        vTaskDelay(pdMS_TO_TICKS(PLAY_TICK_MS));
    }
}
#endif

void melodyInit()
{
    if (!HAVE_SPEAKER) {
        return;
    }
#if defined(ESP8266)
    pinMode(SPEAKER_PIN, OUTPUT);
    digitalWrite(SPEAKER_PIN, LOW);
    schedule_recurrent_function_us([]() {
        playerUpdate();
        return true;
    }, PLAY_TICK_MS * 1000);
#else
    ledcSetup(SPEAKER_CHANNEL, 1000, 10);
    ledcAttachPin(SPEAKER_PIN, SPEAKER_CHANNEL);
    ledcWrite(SPEAKER_CHANNEL, 0);
    xTaskCreatePinnedToCore(playerTask, "melody", 3072, nullptr, 2, nullptr, 1);
#endif
}

bool melodyPlay(const int ix)
{
    const char* text = rtttlMelody(ix);
    if (!HAVE_SPEAKER || (text == nullptr)) {
        return false;
    }
    Lock lock(mutex);
    speakerTone(0);
    toneOn = false;
    playing = -1;
    if (!parser.begin(text)) {
        return false;
    }
    playing = ix;
    startNote(millis());
    return true;
}

/**
 * @brief Add a Morse element, or more silence
 *
 * @param[in] units  > 0 tone, < 0 silence [units]
 */
static void morseAdd(const int8_t units)
{
    if ((units < 0) && (morseNum > 0) && (morse[morseNum - 1] < 0)) {
        morse[morseNum - 1] = (int8_t)min(morse[morseNum - 1], units);
    } else if (morseNum < MORSE_MAX) {
        morse[morseNum++] = units;
    }
}

bool melodyMorse(const char* text)
{
    if (!HAVE_SPEAKER) {
        return false;
    }
    Lock lock(mutex);
    speakerTone(0);
    toneOn = false;
    playing = -1;
    morseNum = 0;
    morseIx = 0;
    for (const char* pos = text; *pos != '\0'; pos++) {
        const char chr = (char)toupper((unsigned char)*pos);
        const char* code = nullptr;
        if ((chr >= 'A') && (chr <= 'Z')) {
            code = MORSE_CODES[chr - 'A'];
        } else if ((chr >= '0') && (chr <= '9')) {
            code = MORSE_CODES[26 + (chr - '0')];
        } else if ((chr == ' ') && (morseNum > 0)) {
            morseAdd(-7);
        }
        for (int ix = 0; (code != nullptr) && (code[ix] != '\0'); ix++) {
            morseAdd((code[ix] == '-') ? 3 : 1);
            morseAdd((code[ix + 1] != '\0') ? -1 : -3);
        }
    }
    playing = PLAYING_MORSE;
    startNote(millis());
    return true;
}

void melodyStop()
{
    if (!HAVE_SPEAKER) {
        return;
    }
    Lock lock(mutex);
    speakerTone(0);
    toneOn = false;
    playing = -1;
}

int melodyPlaying()
{
    Lock lock(mutex);
    return ((playing >= 0) ? playing : -1);
}
