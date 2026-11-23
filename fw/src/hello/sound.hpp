// Sound module: trigger pulse

#ifndef SOUND_HPP
#define SOUND_HPP

/**
 * @brief Init sound pin (off)
 */
void soundInit();

/**
 * @brief Trigger sound module
 *
 * High pulse of SOUND_PULSE_MS on SOUND_PIN.
 */
void soundTrigger();

/**
 * @brief End pulse after its time
 *
 * Call often.
 */
void soundUpdate();

#endif // SOUND_HPP
