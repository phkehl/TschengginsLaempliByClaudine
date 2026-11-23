// RTTTL melodies: built-in list and parser (see rtttl.cpp for the format, sources and credits)

#ifndef RTTTL_HPP
#define RTTTL_HPP

#include <stddef.h>
#include <stdint.h>

// One note of a melody
struct RtttlNote
{
    uint16_t freq;  // Frequency [Hz], 0 = pause
    uint16_t ms;    // Duration [ms]
};

/**
 * @brief Get number of built-in melodies
 */
int rtttlCount();

/**
 * @brief Get a built-in melody
 *
 * @param[in] ix  Melody index (0 ... rtttlCount() - 1)
 *
 * @returns the RTTTL text (in flash, PROGMEM), nullptr for a bad index
 */
const char* rtttlMelody(const int ix);

/**
 * @brief Get name of a built-in melody
 *
 * @param[in]  ix    Melody index
 * @param[out] name  Name buffer
 * @param[in]  size  Size of name (11 is enough for the RTTTL limit, the built-in names have up to 13 chars)
 */
void rtttlName(const int ix, char* name, const size_t size);

/**
 * @brief Get approximate length of a built-in melody
 *
 * @param[in] ix  Melody index
 *
 * @returns the length [s], at least 1, 0 for a bad index
 */
int rtttlSeconds(const int ix);

/**
 * @brief Find a built-in melody by name (ignores case)
 *
 * @returns the melody index, -1 if not found
 */
int rtttlFind(const char* name);

// Parser: reads the notes of one melody, one after the other
class Rtttl
{
  public:
    /**
     * @brief Start a melody: read name and control section
     *
     * @param[in] text  RTTTL text (in flash, PROGMEM)
     *
     * @returns true if the text has a name and a control section
     */
    bool begin(const char* text);

    /**
     * @brief Get the next note
     *
     * Skips bad notes and control pairs in the tone section.
     *
     * @param[out] note  The note
     * @param[in]  freq  false: no frequency (faster, for the length)
     *
     * @returns false at the end of the melody
     */
    bool next(RtttlNote& note, const bool freq = true);

  private:
    const char* pos = nullptr;  // Read position (flash)
    uint8_t duration = 4;       // Default duration (1/n note)
    uint8_t octave = 6;         // Default octave
    uint32_t wholeMs = 3810;    // Time of a whole note [ms] (default 63 bpm)

    char peek();
    char get();
    long number();
    void control(const char name);
};

#endif // RTTTL_HPP
