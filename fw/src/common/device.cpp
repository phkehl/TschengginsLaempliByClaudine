// Device identification

#include <Arduino.h>
#include <fw_version.h>

#include "device.hpp"

const char* deviceId()
{
    static char id[24] = "";
#if defined(ESP8266)
    if (id[0] == '\0') {
        // Chip ID: last 3 bytes of the factory MAC
        snprintf(id, sizeof(id), "%s-%06x", FW_TYPE, (unsigned)ESP.getChipId());
    }
#else
    if (id[0] == '\0') {
        // eFuse MAC: byte 0 is the first MAC byte
        const uint64_t mac = ESP.getEfuseMac();
        uint8_t bytes[6];
        for (int ix = 0; ix < 6; ix++) {
            bytes[ix] = (mac >> (8 * ix)) & 0xff;
        }
        snprintf(id, sizeof(id), "%s-%02x%02x%02x", FW_TYPE, bytes[3], bytes[4], bytes[5]);
    }
#endif
    return id;
}
