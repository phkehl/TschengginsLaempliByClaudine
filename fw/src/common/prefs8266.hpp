// Preferences on the ESP8266: the ESP32 API (subset) on LittleFS
//
// One file per key: /prefs/<namespace>/<key>, the raw value (string without NUL, numbers little endian). Each put
// writes the file at once. LittleFS is formatted at the first start (empty settings). It is mounted from begin() to
// end() only (saves about 1 kB heap for TLS).

#ifndef PREFS8266_HPP
#define PREFS8266_HPP

#include <stddef.h>
#include <stdint.h>

// Settings storage (as the ESP32 Preferences class)
class Preferences
{
  public:
    /**
     * @brief Open namespace
     *
     * @param[in] name      Namespace (max. 15 chars)
     * @param[in] readOnly  Not used
     *
     * @returns true if LittleFS is mounted
     */
    bool begin(const char* name, const bool readOnly = false);

    /**
     * @brief Close namespace
     */
    void end();

    bool isKey(const char* key);
    bool remove(const char* key);
    size_t putString(const char* key, const char* value);
    size_t getString(const char* key, char* value, const size_t maxLen);
    size_t putBool(const char* key, const bool value);
    bool getBool(const char* key, const bool defaultValue = false);
    size_t putUChar(const char* key, const uint8_t value);
    uint8_t getUChar(const char* key, const uint8_t defaultValue = 0);
    size_t putUShort(const char* key, const uint16_t value);
    uint16_t getUShort(const char* key, const uint16_t defaultValue = 0);

  private:
    char ns[16] = "";

    /**
     * @brief Get file path of a key
     */
    void path(const char* key, char* buf, const size_t size);

    /**
     * @brief Write value file
     *
     * @returns the number of bytes written, 0 on error
     */
    size_t write(const char* key, const void* data, const size_t len);

    /**
     * @brief Read value file
     *
     * @returns the number of bytes read, -1 if no such key
     */
    int read(const char* key, void* data, const size_t len);
};

#endif // PREFS8266_HPP
