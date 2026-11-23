// Configuration shell on the serial port

#ifndef SHELL_HPP
#define SHELL_HPP

#include <stdint.h>

/**
 * @brief Print welcome and prompt
 */
void shellInit();

/**
 * @brief Handle serial input
 *
 * Call often (every 20 ms or faster).
 */
void shellUpdate();

/**
 * @brief Parse a number
 *
 * @param[in] str  Text (digits only)
 *
 * @returns the value, -1 if invalid
 */
long shellParseNumber(const char* str);

// Implemented by each firmware: more commands, help, status and config lines

/**
 * @brief Print help lines of the firmware commands
 */
void shellExtHelp();

/**
 * @brief Run a firmware command
 *
 * @param[in] cmd   Command (first word)
 * @param[in] sub   Second word, nullptr if none
 * @param[in] arg1  Third word, nullptr if none
 * @param[in] arg2  Fourth word, nullptr if none
 * @param[in] arg3  Fifth word, nullptr if none
 *
 * @returns true if the firmware has this command
 */
bool shellExtCommand(const char* cmd, const char* sub, const char* arg1, const char* arg2, const char* arg3);

/**
 * @brief Print status lines of the firmware ("status")
 */
void shellExtStatus();

/**
 * @brief Print settings of the firmware ("config")
 */
void shellExtConfig();

#endif // SHELL_HPP
