// Device identification

#ifndef DEVICE_HPP
#define DEVICE_HPP

/**
 * @brief Get device ID
 *
 * <type>-<6 hex chars>: firmware type (FW_TYPE, e.g. "chlotz") and the last 3 bytes of the factory MAC address
 * (eFuse), lower case. Fixed for each chip.
 *
 * @returns the device ID, e.g. "chlotz-96c480"
 */
const char* deviceId();

#endif // DEVICE_HPP
