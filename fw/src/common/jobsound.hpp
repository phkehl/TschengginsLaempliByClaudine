// Job sounds: melody or sound effect when a job stops (see jobsound.cpp)

#ifndef JOBSOUND_HPP
#define JOBSOUND_HPP

#include <stdint.h>

#include <ArduinoJson.h>

#include "backend.hpp"

// Sound IDs: melody index (rtttl.hpp), or one of these
static constexpr int8_t SOUND_NONE = -1;
static constexpr int8_t SOUND_FX = -2;  // Sound effect (Hello: sound module)

/**
 * @brief Find a sound by name
 *
 * @param[in] name  Melody name (any case) or "fx"
 *
 * @returns the sound ID, SOUND_NONE if unknown
 */
int8_t jobSoundFind(const char* name);

/**
 * @brief Parse the sounds of an LED
 *
 * @param[in]  json    The "sounds" object of the LED state ({event: sound}), may be null
 * @param[out] sounds  Sound ID for each event (JobSound), SOUND_NONE for unknown sounds
 */
void jobSoundParse(JsonObjectConst json, int8_t* sounds);

/**
 * @brief Select the sound for a new state
 *
 * Plays one sound: the one of the most important event, the lowest LED first.
 *
 * @param[in] before  LED states before
 * @param[in] now     LED states now
 * @param[in] num     Number of LEDs
 *
 * @returns the sound ID, SOUND_NONE if none
 */
int8_t jobSoundSelect(const LedInfo* before, const LedInfo* now, const int num);

/**
 * @brief Play a sound
 *
 * @param[in] sound  Sound ID
 */
void jobSoundPlay(const int8_t sound);

/**
 * @brief Add sound options (for the web page)
 *
 * @param[out] obj  Options: "melodies" ["<name>:<length [s]>", ...]
 */
void jobSoundOptions(JsonObject obj);

/**
 * @brief Get name of a sound
 *
 * @param[in]  sound  Sound ID
 * @param[out] name   Name buffer ("" for SOUND_NONE)
 * @param[in]  size   Size of name
 */
void jobSoundName(const int8_t sound, char* name, const size_t size);

/**
 * @brief Play the sound effect: implemented by each firmware
 *
 * @returns false if the device has no sound effect
 */
bool soundFxPlay();

#endif // JOBSOUND_HPP
