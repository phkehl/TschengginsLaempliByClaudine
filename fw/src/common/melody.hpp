// Melody player: built-in RTTTL melodies on the speaker (see melody.cpp)

#ifndef MELODY_HPP
#define MELODY_HPP

/**
 * @brief Init speaker pin and player
 *
 * ESP32: starts the player task. ESP8266: adds the player as scheduled function.
 */
void melodyInit();

/**
 * @brief Play a built-in melody
 *
 * Stops a melody that plays.
 *
 * @param[in] ix  Melody index (see rtttl.hpp)
 *
 * @returns false for a bad index
 */
bool melodyPlay(const int ix);

// Boot sound (Morse code), see melodyMorse()
static constexpr const char* BOOT_MORSE = "FFI";

/**
 * @brief Play a text in Morse code
 *
 * Stops a melody that plays. Letters A-Z, digits, spaces (word gap); other characters are left out.
 *
 * @param[in] text  The text
 *
 * @returns false without speaker
 */
bool melodyMorse(const char* text);

/**
 * @brief Stop the melody (or Morse code)
 */
void melodyStop();

/**
 * @brief Get the melody that plays
 *
 * @returns the melody index, -1 if none plays (also for Morse code)
 */
int melodyPlaying();

#endif // MELODY_HPP
