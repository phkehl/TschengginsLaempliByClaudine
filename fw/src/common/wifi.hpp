// WiFi station with network list in NVS

#ifndef WIFI_HPP
#define WIFI_HPP

#include <stdint.h>

#include <IPAddress.h>

// WiFi connection state
enum class WifiState
{
    NONE,        // No network configured
    IDLE,        // Paused for user scan
    SCANNING,    // Scanning before connect round
    CONNECTING,  // Connecting to a network
    CONNECTED,   // Connected, has IP address
};

// One scan result
struct WifiNet
{
    char ssid[33];
    int8_t rssi;
    bool secure;
};

// One configured network
struct WifiCfg
{
    char ssid[33];
    char pass[64];
};

/**
 * @brief Init WiFi, start connecting
 */
void wifiInit();

/**
 * @brief Run WiFi state machine
 */
void wifiUpdate();

/**
 * @brief Get number of configured networks
 *
 * @returns the number of networks
 */
int wifiNumNets();

/**
 * @brief Get configured network
 *
 * @param[in] ix  Index (0 = first tried)
 *
 * @returns the network
 */
const WifiCfg& wifiGetNet(const int ix);

/**
 * @brief Add or update network
 *
 * A new network goes to the end of the list. An existing SSID gets the new password.
 *
 * @param[in] ssid  Network name (1-32 chars)
 * @param[in] pass  Password, empty for open networks
 *
 * @returns true on success, false if list is full or input is invalid
 */
bool wifiAddNet(const char* ssid, const char* pass);

/**
 * @brief Delete network
 *
 * @param[in] ix  Index
 *
 * @returns true on success
 */
bool wifiDelNet(const int ix);

/**
 * @brief Move network in list
 *
 * @param[in] from  Old index
 * @param[in] to    New index
 *
 * @returns true on success
 */
bool wifiMoveNet(const int from, const int to);

/**
 * @brief Check password length
 *
 * @param[in] pass  Password
 *
 * @returns true if empty (open) or 8-63 chars
 */
bool wifiPassValid(const char* pass);

/**
 * @brief Start new connect round
 */
void wifiReconnect();

/**
 * @brief Get connection state
 *
 * @returns the state
 */
WifiState wifiGetState();

/**
 * @brief Get current network name
 *
 * @returns the SSID (connecting or connected), empty string if none
 */
const char* wifiGetSsid();

/**
 * @brief Get IP address
 *
 * @returns the IP address, valid if connected
 */
IPAddress wifiGetIp();

/**
 * @brief Get signal strength
 *
 * @returns the RSSI [dBm], valid if connected
 */
int wifiGetRssi();

/**
 * @brief Start user network scan
 *
 * Pauses connection attempts if not connected. Use wifiResume() to start them again.
 */
void wifiScanStart();

/**
 * @brief Get user scan result
 *
 * Results are unique by SSID, sorted by signal strength.
 *
 * @param[out] nets  Scan results
 * @param[in]  max   Size of nets
 *
 * @returns the number of networks, -1 if scan runs, -2 if scan failed
 */
int wifiScanResult(WifiNet* nets, const int max);

/**
 * @brief Resume connecting after user scan
 */
void wifiResume();

#endif // WIFI_HPP
