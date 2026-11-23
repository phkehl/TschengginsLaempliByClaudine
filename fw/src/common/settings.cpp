// Settings in NVS: backend and device name
//
// NVS namespace "cfg", keys: "url", "user", "pass", "insecure", "name". The firmware can have more keys in the same
// namespace (e.g. chlotzsettings.cpp). Each setter saves at once. The backend settings and the device name are guarded
// by a mutex, because other tasks read them.
//
// Synced settings (device name and the synced settings of the firmware, also set by the backend, see backend.cpp):
// each change increments a counter (settingsRev()). The backend then reports them, the UI draws them again.

#include <Arduino.h>

#include "compat.hpp"
#include "settings.hpp"

static Mutex mtx;
static BackendCfg backend = {};
static uint32_t backendVersion = 0;
static char deviceName[DEVICE_NAME_MAX + 1] = "";
static Atomic<uint32_t> syncRev(0);

/**
 * @brief Save backend settings to NVS
 */
static void saveBackend()
{
    Preferences prefs;
    prefs.begin("cfg", false);
    prefs.putString("url", backend.url);
    prefs.putString("user", backend.user);
    prefs.putString("pass", backend.pass);
    prefs.putBool("insecure", backend.insecure);
    prefs.end();
    backendVersion++;
}

void settingsInit()
{
    Preferences prefs;
    prefs.begin("cfg", false);
    if (prefs.isKey("url")) {
        prefs.getString("url", backend.url, sizeof(backend.url));
        prefs.getString("user", backend.user, sizeof(backend.user));
        prefs.getString("pass", backend.pass, sizeof(backend.pass));
        backend.insecure = prefs.getBool("insecure", false);
    }
    prefs.getString("name", deviceName, sizeof(deviceName));
    prefs.end();
}

uint32_t settingsGetBackend(BackendCfg& cfg)
{
    Lock lock(mtx);
    cfg = backend;
    return backendVersion;
}

bool settingsSetUrl(const char* url)
{
    const size_t len = strlen(url);
    const bool valid = ((len == 0) || (((strncmp(url, "http://", 7) == 0) && (len > 7)) ||
        ((strncmp(url, "https://", 8) == 0) && (len > 8))));
    if (!valid || (len >= sizeof(backend.url))) {
        return false;
    }
    Lock lock(mtx);
    strlcpy(backend.url, url, sizeof(backend.url));
    backend.insecure = false;
    saveBackend();
    return true;
}

bool settingsSetUser(const char* user)
{
    if (strlen(user) >= sizeof(backend.user)) {
        return false;
    }
    Lock lock(mtx);
    strlcpy(backend.user, user, sizeof(backend.user));
    saveBackend();
    return true;
}

bool settingsSetPass(const char* pass)
{
    if (strlen(pass) >= sizeof(backend.pass)) {
        return false;
    }
    Lock lock(mtx);
    strlcpy(backend.pass, pass, sizeof(backend.pass));
    saveBackend();
    return true;
}

void settingsSetInsecure(const bool insecure)
{
    Lock lock(mtx);
    backend.insecure = insecure;
    saveBackend();
}

void settingsGetName(char* name, const size_t size)
{
    Lock lock(mtx);
    strlcpy(name, deviceName, size);
}

/**
 * @brief Get length of UTF-8 character
 *
 * @param[in] str    The character (non-ASCII)
 * @param[in] avail  Number of bytes in str
 *
 * @returns the number of bytes, 0 if not valid (control character, overlong, surrogate, ...)
 */
static size_t utf8CharLen(const unsigned char* str, const size_t avail)
{
    const unsigned char ch = str[0];
    unsigned char low = 0x80;
    unsigned char high = 0xbf;
    size_t len = 0;
    if ((ch >= 0xc2) && (ch <= 0xdf)) {
        len = 2;
        low = ((ch == 0xc2) ? 0xa0 : 0x80);
    } else if ((ch >= 0xe0) && (ch <= 0xef)) {
        len = 3;
        low = ((ch == 0xe0) ? 0xa0 : 0x80);
        high = ((ch == 0xed) ? 0x9f : 0xbf);
    } else if ((ch >= 0xf0) && (ch <= 0xf4)) {
        len = 4;
        low = ((ch == 0xf0) ? 0x90 : 0x80);
        high = ((ch == 0xf4) ? 0x8f : 0xbf);
    }
    if ((len == 0) || (len > avail) || (str[1] < low) || (str[1] > high)) {
        return 0;
    }
    for (size_t ix = 2; ix < len; ix++) {
        if ((str[ix] & 0xc0) != 0x80) {
            return 0;
        }
    }
    return len;
}

bool settingsSetName(const char* name)
{
    // Trim spaces
    while (*name == ' ') {
        name++;
    }
    size_t len = strlen(name);
    while ((len > 0) && (name[len - 1] == ' ')) {
        len--;
    }
    if (len > DEVICE_NAME_MAX) {
        return false;
    }
    for (size_t ix = 0; ix < len; ) {
        const unsigned char ch = name[ix];
        if (ch >= 0x80) {
            const size_t charLen = utf8CharLen((const unsigned char*)&name[ix], (len - ix));
            if (charLen == 0) {
                return false;
            }
            ix += charLen;
        } else if (isalnum(ch) || (strchr(" -_.", (char)ch) != nullptr)) {
            ix++;
        } else {
            return false;
        }
    }
    Lock lock(mtx);
    memcpy(deviceName, name, len);
    deviceName[len] = '\0';
    Preferences prefs;
    prefs.begin("cfg", false);
    prefs.putString("name", deviceName);
    prefs.end();
    syncRev++;
    return true;
}

uint32_t settingsRev()
{
    return syncRev.load();
}

void settingsChanged()
{
    syncRev++;
}
