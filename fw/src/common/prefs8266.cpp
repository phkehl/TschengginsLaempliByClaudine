// Preferences on the ESP8266: the ESP32 API (subset) on LittleFS

#if defined(ESP8266)

#include <Arduino.h>
#include <LittleFS.h>

#include "prefs8266.hpp"

/**
 * @brief Mount LittleFS
 *
 * @returns true if mounted
 */
static bool mountFs()
{
    // Formats if not yet formatted (auto format)
    const bool mounted = LittleFS.begin();
    if (!mounted) {
        Serial.println("Prefs: LittleFS mount failed");
    }
    return mounted;
}

bool Preferences::begin(const char* name, const bool readOnly)
{
    (void)readOnly;
    strlcpy(ns, name, sizeof(ns));
    if (!mountFs()) {
        return false;
    }
    char dir[24];
    snprintf(dir, sizeof(dir), "/prefs/%s", ns);
    if (!LittleFS.exists(dir)) {
        LittleFS.mkdir(dir);
    }
    return true;
}

void Preferences::end()
{
    ns[0] = '\0';
    LittleFS.end();
}

void Preferences::path(const char* key, char* buf, const size_t size)
{
    snprintf(buf, size, "/prefs/%s/%s", ns, key);
}

size_t Preferences::write(const char* key, const void* data, const size_t len)
{
    char name[48];
    path(key, name, sizeof(name));
    File file = LittleFS.open(name, "w");
    if (!file) {
        return 0;
    }
    const size_t num = file.write((const uint8_t*)data, len);
    file.close();
    return ((num == len) ? num : 0);
}

int Preferences::read(const char* key, void* data, const size_t len)
{
    char name[48];
    path(key, name, sizeof(name));
    File file = LittleFS.open(name, "r");
    if (!file) {
        return -1;
    }
    const int num = file.read((uint8_t*)data, len);
    file.close();
    return num;
}

bool Preferences::isKey(const char* key)
{
    char name[48];
    path(key, name, sizeof(name));
    return LittleFS.exists(name);
}

bool Preferences::remove(const char* key)
{
    char name[48];
    path(key, name, sizeof(name));
    return LittleFS.remove(name);
}

size_t Preferences::putString(const char* key, const char* value)
{
    // Empty string: file with no data
    const size_t len = strlen(value);
    return ((write(key, value, len) == len) ? (len + 1) : 0);
}

size_t Preferences::getString(const char* key, char* value, const size_t maxLen)
{
    if (maxLen == 0) {
        return 0;
    }
    const int num = read(key, value, maxLen - 1);
    if (num < 0) {
        value[0] = '\0';
        return 0;
    }
    value[num] = '\0';
    return num + 1;
}

size_t Preferences::putBool(const char* key, const bool value)
{
    return putUChar(key, (value ? 1 : 0));
}

bool Preferences::getBool(const char* key, const bool defaultValue)
{
    return (getUChar(key, (defaultValue ? 1 : 0)) != 0);
}

size_t Preferences::putUChar(const char* key, const uint8_t value)
{
    return write(key, &value, sizeof(value));
}

uint8_t Preferences::getUChar(const char* key, const uint8_t defaultValue)
{
    uint8_t value = 0;
    return ((read(key, &value, sizeof(value)) == (int)sizeof(value)) ? value : defaultValue);
}

size_t Preferences::putUShort(const char* key, const uint16_t value)
{
    return write(key, &value, sizeof(value));
}

uint16_t Preferences::getUShort(const char* key, const uint16_t defaultValue)
{
    uint16_t value = 0;
    return ((read(key, &value, sizeof(value)) == (int)sizeof(value)) ? value : defaultValue);
}

#endif // ESP8266
