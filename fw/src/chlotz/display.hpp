// Display and touch hardware of the CYD

#ifndef DISPLAY_HPP
#define DISPLAY_HPP

#include <stdint.h>

#include <LovyanGFX.hpp>

// Text grid of the default font, in pixels
static constexpr int FONT_W = 8;
static constexpr int FONT_H = 16;

// Default font: Spleen 8x16
extern const lgfx::U8g2font fontSpleen;

/**
 * @brief Init display, backlight and touch
 */
void displayInit();

/**
 * @brief Apply display flip setting
 *
 * Call often, from the UI code only. The screen is black after a change.
 *
 * @returns true if the rotation changed: draw the screen again
 */
bool displayFlipUpdate();

/**
 * @brief Get display device
 *
 * @returns the LovyanGFX device
 */
lgfx::LGFX_Device& displayLcd();

/**
 * @brief Get number of text characters
 *
 * @param[in] text  Text (UTF-8)
 *
 * @returns the number of characters, each is FONT_W wide
 */
int displayTextLen(const char* text);

/**
 * @brief Get touch position
 *
 * @param[out] x  Touch x position [px]
 * @param[out] y  Touch y position [px]
 *
 * @returns true if the display is touched
 */
bool displayTouch(int& x, int& y);

/**
 * @brief Register user activity
 *
 * Restores full brightness and restarts the dim timer.
 *
 * @returns true if the display was dimmed
 */
bool displayActivity();

/**
 * @brief Dim display after timeout
 *
 * Call often. Uses the dim timeout and level from the settings.
 */
void displayDimUpdate();

/**
 * @brief Draw a button
 *
 * @param[in] x        Left edge [px]
 * @param[in] y        Top edge [px]
 * @param[in] w        Width [px]
 * @param[in] h        Height [px]
 * @param[in] label    Button text
 * @param[in] pressed  Draw pressed state
 * @param[in] active   Draw active (toggled) state
 */
void displayButton(const int x, const int y, const int w, const int h, const char* label, const bool pressed,
    const bool active = false);

#endif // DISPLAY_HPP
