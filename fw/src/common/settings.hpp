// Settings in NVS: backend and device name

#ifndef SETTINGS_HPP
#define SETTINGS_HPP

#include <stddef.h>
#include <stdint.h>

// Backend settings
struct BackendCfg
{
    char url[128];   // http:// or https://, empty = no backend
    char user[33];   // Basic auth user, empty = no auth
    char pass[65];   // Basic auth password
    bool insecure;   // No certificate check
};

/**
 * @brief Load settings from NVS
 */
void settingsInit();

/**
 * @brief Get copy of backend settings
 *
 * Thread-safe.
 *
 * @param[out] cfg  Backend settings
 *
 * @returns the backend settings version (changes on each change)
 */
uint32_t settingsGetBackend(BackendCfg& cfg);

/**
 * @brief Set backend URL
 *
 * Also clears the insecure flag.
 *
 * @param[in] url  URL (http:// or https://, max. 127 chars), empty for no backend
 *
 * @returns true on success, false if invalid
 */
bool settingsSetUrl(const char* url);

/**
 * @brief Set basic auth user
 *
 * @param[in] user  User (max. 32 chars), empty for no auth
 *
 * @returns true on success, false if invalid
 */
bool settingsSetUser(const char* user);

/**
 * @brief Set basic auth password
 *
 * @param[in] pass  Password (max. 64 chars)
 *
 * @returns true on success, false if invalid
 */
bool settingsSetPass(const char* pass);

/**
 * @brief Set certificate check
 *
 * @param[in] insecure  true: no certificate check
 */
void settingsSetInsecure(const bool insecure);

// Max. device name length, in bytes (UTF-8)
static constexpr int DEVICE_NAME_MAX = 25;

/**
 * @brief Get device name
 *
 * Thread-safe.
 *
 * @param[out] name  Device name, empty if not set
 * @param[in]  size  Size of name (DEVICE_NAME_MAX + 1)
 */
void settingsGetName(char* name, const size_t size);

/**
 * @brief Set device name
 *
 * Leading and trailing spaces are removed.
 *
 * @param[in] name  Name (max. DEVICE_NAME_MAX bytes UTF-8: letters, digits, space, - _ ., non-ASCII), empty for none
 *
 * @returns true on success, false if invalid
 */
bool settingsSetName(const char* name);

/**
 * @brief Get change counter of the synced settings
 *
 * Name and the synced settings of the firmware (see syncsettings.hpp). Thread-safe.
 *
 * @returns the counter, incremented on each change
 */
uint32_t settingsRev();

/**
 * @brief Count a change of a synced setting
 *
 * For the synced settings of the firmware. The backend then reports them.
 */
void settingsChanged();

#endif // SETTINGS_HPP
