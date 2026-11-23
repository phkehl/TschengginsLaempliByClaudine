// LED test: sequence of test patterns, rainbow demo

#ifndef LEDTEST_HPP
#define LEDTEST_HPP

#include <stdint.h>

#include <FastLED.h>

/**
 * @brief Start LED test
 *
 * Starts at the first step, playing.
 */
void ledTestStart();

/**
 * @brief Stop LED test
 */
void ledTestStop();

/**
 * @brief Check if LED test runs
 *
 * @returns true if started (playing or paused)
 */
bool ledTestActive();

/**
 * @brief Play or pause the sequence
 *
 * Pause stops the step change. Animated steps keep running.
 *
 * @param[in] play  true to play, false to pause
 */
void ledTestPlay(const bool play);

/**
 * @brief Check if sequence plays
 *
 * @returns true if playing, false if paused
 */
bool ledTestPlaying();

/**
 * @brief Go to next or previous step
 *
 * Wraps around at the ends.
 *
 * @param[in] delta  +1 for next, -1 for previous
 */
void ledTestStep(const int delta);

/**
 * @brief Get current step number
 *
 * @returns the step (0 ... ledTestNumSteps() - 1)
 */
int ledTestIndex();

/**
 * @brief Get number of steps
 *
 * @returns the number of steps
 */
int ledTestNumSteps();

/**
 * @brief Get step name
 *
 * @param[out] buf   Text buffer
 * @param[in]  size  Size of buf
 */
void ledTestName(char* buf, const size_t size);

/**
 * @brief Make LED colours of current step
 *
 * Call for each LED update. Changes the step if playing.
 *
 * @param[out] leds   LED colours
 * @param[in]  num    Number of LEDs
 * @param[out] scale  Global brightness (0-255)
 *
 * @returns true if the test is active (leds and scale set), else false (nothing changed)
 */
bool ledTestRender(CRGB* leds, const int num, uint8_t& scale);

/**
 * @brief Start rainbow demo
 */
void ledDemoStart();

/**
 * @brief Stop rainbow demo
 */
void ledDemoStop();

/**
 * @brief Check if rainbow demo runs
 *
 * @returns true if started
 */
bool ledDemoActive();

/**
 * @brief Make rainbow demo LED colours
 *
 * A rainbow over the LEDs that moves. Works also if not started (shell "led demo").
 *
 * @param[out] leds   LED colours
 * @param[in]  num    Number of LEDs
 */
void ledDemoRender(CRGB* leds, const int num);

#endif // LEDTEST_HPP
