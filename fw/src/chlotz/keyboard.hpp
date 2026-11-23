// On-screen keyboard

#ifndef KEYBOARD_HPP
#define KEYBOARD_HPP

// Result of a key press
enum class KbResult
{
    NONE,    // Continue input
    OK,      // Input done
    CANCEL,  // Input cancelled
};

/**
 * @brief Start new input
 *
 * @param[in] title    Text above the input field
 * @param[in] initial  Initial text
 * @param[in] maxLen   Max. text length (1-127)
 */
void kbStart(const char* title, const char* initial, const int maxLen);

/**
 * @brief Change title, keep input
 *
 * @param[in] title  Text above the input field
 */
void kbSetTitle(const char* title);

/**
 * @brief Draw the full keyboard screen
 */
void kbDraw();

/**
 * @brief Find key at position
 *
 * @param[in] x  Touch x position [px]
 * @param[in] y  Touch y position [px]
 *
 * @returns the key ID, -1 for no key
 */
int kbHitKey(const int x, const int y);

/**
 * @brief Draw one key
 *
 * @param[in] id       Key ID
 * @param[in] pressed  Draw pressed state
 */
void kbDrawKey(const int id, const bool pressed);

/**
 * @brief Handle key press
 *
 * @param[in] id  Key ID
 *
 * @returns the input state
 */
KbResult kbPressKey(const int id);

/**
 * @brief Get input text
 *
 * @returns the text
 */
const char* kbGetText();

#endif // KEYBOARD_HPP
