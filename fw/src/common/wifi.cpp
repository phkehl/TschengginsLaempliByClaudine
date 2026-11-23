// WiFi station with network list in NVS
//
// Only station mode. Up to WIFI_MAX_NETS networks are saved in NVS (namespace "wifi", key "num" for the number,
// keys "s0", "p0", "s1", "p1", ... for SSID and password). The ESP-IDF WiFi storage is not used
// (WiFi.persistent(false)). The single network of older firmware (keys "ssid", "pass") is moved into the list.
//
// Connect round:
// 1. Scan for networks
// 2. Try the configured networks found by the scan, in list order
// 3. Try the configured networks not found by the scan (they can be hidden), in list order
// 4. Use the first that connects. If none connects, start a new round.
//
// Each network gets WIFI_CONNECT_MS to connect. If the connection is lost, a new round starts.
//
// Hostname (DHCP): the device ID, e.g. chlotz-96c480.
//
// ESP8266 (NVS: see prefs8266.cpp): scanComplete() works there, no scan done event needed.

#include <Arduino.h>

#if defined(ESP8266)
#include <ESP8266WiFi.h>
#else
#include <WiFi.h>
#endif

#include "compat.hpp"
#include "config.hpp"
#include "device.hpp"
#include "wifi.hpp"

static WifiCfg cfgs[WIFI_MAX_NETS];
static int numCfgs = 0;

static int order[WIFI_MAX_NETS];
static int numOrder = 0;
static int orderPos = 0;

static volatile bool scanDone = false;
static uint32_t scanTime = 0;

static WifiState state = WifiState::NONE;
static uint32_t stateTime = 0;
static char curSsid[33] = "";

// ---------------------------------------------------------------------------------------------------------------------

/**
 * @brief Load network list from NVS
 */
static void loadNets()
{
    Preferences prefs;
    prefs.begin("wifi", false);
    numCfgs = 0;
    const int num = min((int)prefs.getUChar("num", 0), WIFI_MAX_NETS);
    for (int ix = 0; ix < num; ix++) {
        char key[8];
        snprintf(key, sizeof(key), "s%d", ix);
        prefs.getString(key, cfgs[numCfgs].ssid, sizeof(cfgs[numCfgs].ssid));
        snprintf(key, sizeof(key), "p%d", ix);
        prefs.getString(key, cfgs[numCfgs].pass, sizeof(cfgs[numCfgs].pass));
        if (cfgs[numCfgs].ssid[0] != '\0') {
            numCfgs++;
        }
    }

    // Move network of older firmware
    if ((numCfgs == 0) && prefs.isKey("ssid")) {
        prefs.getString("ssid", cfgs[0].ssid, sizeof(cfgs[0].ssid));
        prefs.getString("pass", cfgs[0].pass, sizeof(cfgs[0].pass));
        if (cfgs[0].ssid[0] != '\0') {
            numCfgs = 1;
        }
        prefs.remove("ssid");
        prefs.remove("pass");
        prefs.end();
        Serial.println("WiFi: moved old network to list");
        return;
    }
    prefs.end();
}

/**
 * @brief Save network list to NVS
 */
static void saveNets()
{
    Preferences prefs;
    prefs.begin("wifi", false);
    prefs.putUChar("num", numCfgs);
    for (int ix = 0; ix < WIFI_MAX_NETS; ix++) {
        char sKey[8];
        char pKey[8];
        snprintf(sKey, sizeof(sKey), "s%d", ix);
        snprintf(pKey, sizeof(pKey), "p%d", ix);
        if (ix < numCfgs) {
            prefs.putString(sKey, cfgs[ix].ssid);
            prefs.putString(pKey, cfgs[ix].pass);
        } else if (prefs.isKey(sKey)) {
            prefs.remove(sKey);
            prefs.remove(pKey);
        }
    }
    prefs.end();
}

/**
 * @brief Start async scan
 */
static void startScan()
{
    WiFi.scanDelete();
    scanDone = false;
    scanTime = millis();
    WiFi.scanNetworks(true);
}

/**
 * @brief Get async scan state
 *
 * The Arduino scanComplete() gives up after 6 s, but a scan can take longer. Wait for the scan done event instead.
 *
 * @returns the number of results, WIFI_SCAN_RUNNING or WIFI_SCAN_FAILED
 */
static int16_t pollScan()
{
#if defined(ESP8266)
    scanDone = (WiFi.scanComplete() != WIFI_SCAN_RUNNING);
#endif
    if (!scanDone) {
        return (((millis() - scanTime) < WIFI_SCAN_MS) ? WIFI_SCAN_RUNNING : WIFI_SCAN_FAILED);
    }
    return WiFi.scanComplete();
}

/**
 * @brief Start connect round with scan
 */
static void startRound()
{
    // Only stop a running connection
    if ((state == WifiState::CONNECTING) || (state == WifiState::CONNECTED)) {
        WiFi.disconnect();
    }
    curSsid[0] = '\0';
    if (numCfgs == 0) {
        state = WifiState::NONE;
        return;
    }
    startScan();
    state = WifiState::SCANNING;
    stateTime = millis();
}

/**
 * @brief Try next network of round
 */
static void tryNext()
{
    if (orderPos >= numOrder) {
        startRound();
        return;
    }
    const WifiCfg& cfg = cfgs[order[orderPos]];
    orderPos++;
    strlcpy(curSsid, cfg.ssid, sizeof(curSsid));
    WiFi.disconnect();
    WiFi.begin(cfg.ssid, cfg.pass);
    state = WifiState::CONNECTING;
    stateTime = millis();
    Serial.printf("WiFi: connecting %s\r\n", curSsid);
}

/**
 * @brief Build try order from scan
 *
 * @param[in] res  Scan result (number of networks or error)
 */
static void buildOrder(const int16_t res)
{
    bool seen[WIFI_MAX_NETS] = {};
    for (int16_t sx = 0; sx < res; sx++) {
        const String ssid = WiFi.SSID(sx);
        for (int ix = 0; ix < numCfgs; ix++) {
            if (ssid == cfgs[ix].ssid) {
                seen[ix] = true;
            }
        }
    }
    WiFi.scanDelete();

    // Networks in range first
    numOrder = 0;
    for (int pass = 0; pass < 2; pass++) {
        for (int ix = 0; ix < numCfgs; ix++) {
            if (seen[ix] == (pass == 0)) {
                order[numOrder] = ix;
                numOrder++;
            }
        }
    }
    orderPos = 0;
}

/**
 * @brief React to network list change
 */
static void listChanged()
{
    saveNets();

    // Keep a working connection
    if (state == WifiState::CONNECTED) {
        for (int ix = 0; ix < numCfgs; ix++) {
            if (strcmp(cfgs[ix].ssid, curSsid) == 0) {
                return;
            }
        }
    }
    if (state != WifiState::IDLE) {
        startRound();
    }
}

// ---------------------------------------------------------------------------------------------------------------------

void wifiInit()
{
    loadNets();
    WiFi.persistent(false);
#if defined(ESP8266)
    WiFi.hostname(deviceId());
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(false);
    Serial.printf("WiFi: %d networks configured, hostname %s\r\n", numCfgs, WiFi.hostname().c_str());
#else
    WiFi.setHostname(deviceId());
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(false);
    WiFi.onEvent([](arduino_event_id_t) { scanDone = true; }, ARDUINO_EVENT_WIFI_SCAN_DONE);
    Serial.printf("WiFi: %d networks configured, hostname %s\r\n", numCfgs, WiFi.getHostname());
#endif
    startRound();
}

void wifiUpdate()
{
    const uint32_t now = millis();
    switch (state) {
        case WifiState::SCANNING: {
            const int16_t res = pollScan();
            if (res != WIFI_SCAN_RUNNING) {
                buildOrder(res);
                tryNext();
            }
            break;
        }
        case WifiState::CONNECTING:
            if (WiFi.status() == WL_CONNECTED) {
                state = WifiState::CONNECTED;
                Serial.printf("WiFi: connected %s, %s\r\n", curSsid, WiFi.localIP().toString().c_str());
            } else if ((now - stateTime) >= WIFI_CONNECT_MS) {
                Serial.printf("WiFi: failed %s\r\n", curSsid);
                tryNext();
            }
            break;
        case WifiState::CONNECTED:
            if (WiFi.status() != WL_CONNECTED) {
                Serial.println("WiFi: connection lost");
                startRound();
            }
            break;
        case WifiState::NONE:
        case WifiState::IDLE:
            break;
    }
}

int wifiNumNets()
{
    return numCfgs;
}

const WifiCfg& wifiGetNet(const int ix)
{
    return cfgs[constrain(ix, 0, WIFI_MAX_NETS - 1)];
}

bool wifiAddNet(const char* ssid, const char* pass)
{
    const size_t len = strlen(ssid);
    if ((len == 0) || (len > 32) || !wifiPassValid(pass)) {
        return false;
    }
    for (int ix = 0; ix < numCfgs; ix++) {
        if (strcmp(cfgs[ix].ssid, ssid) == 0) {
            strlcpy(cfgs[ix].pass, pass, sizeof(cfgs[ix].pass));
            listChanged();
            return true;
        }
    }
    if (numCfgs >= WIFI_MAX_NETS) {
        return false;
    }
    strlcpy(cfgs[numCfgs].ssid, ssid, sizeof(cfgs[numCfgs].ssid));
    strlcpy(cfgs[numCfgs].pass, pass, sizeof(cfgs[numCfgs].pass));
    numCfgs++;
    listChanged();
    return true;
}

bool wifiDelNet(const int ix)
{
    if ((ix < 0) || (ix >= numCfgs)) {
        return false;
    }
    for (int jx = ix; jx < (numCfgs - 1); jx++) {
        cfgs[jx] = cfgs[jx + 1];
    }
    numCfgs--;
    listChanged();
    return true;
}

bool wifiMoveNet(const int from, const int to)
{
    if ((from < 0) || (from >= numCfgs) || (to < 0) || (to >= numCfgs)) {
        return false;
    }
    const WifiCfg cfg = cfgs[from];
    if (from < to) {
        for (int ix = from; ix < to; ix++) {
            cfgs[ix] = cfgs[ix + 1];
        }
    } else {
        for (int ix = from; ix > to; ix--) {
            cfgs[ix] = cfgs[ix - 1];
        }
    }
    cfgs[to] = cfg;
    listChanged();
    return true;
}

bool wifiPassValid(const char* pass)
{
    const size_t len = strlen(pass);
    return ((len == 0) || ((len >= 8) && (len <= 63)));
}

void wifiReconnect()
{
    startRound();
}

WifiState wifiGetState()
{
    return state;
}

const char* wifiGetSsid()
{
    return curSsid;
}

IPAddress wifiGetIp()
{
    return WiFi.localIP();
}

int wifiGetRssi()
{
    return WiFi.RSSI();
}

void wifiScanStart()
{
    // Connection attempts disturb the scan
    if (state == WifiState::CONNECTING) {
        WiFi.disconnect();
    }
    if (state != WifiState::CONNECTED) {
        curSsid[0] = '\0';
        state = WifiState::IDLE;
    }
    startScan();
}

int wifiScanResult(WifiNet* nets, const int max)
{
    const int16_t res = pollScan();
    if (res == WIFI_SCAN_RUNNING) {
        return -1;
    }
    if (res < 0) {
        return -2;
    }

    // Keep strongest entry per SSID
    int num = 0;
    for (int16_t ix = 0; ix < res; ix++) {
        const String ssid = WiFi.SSID(ix);
        if (ssid.isEmpty()) {
            continue;
        }
        const int8_t rssi = WiFi.RSSI(ix);
        int found = -1;
        for (int jx = 0; jx < num; jx++) {
            if (ssid == nets[jx].ssid) {
                found = jx;
                break;
            }
        }
        if (found >= 0) {
            if (rssi > nets[found].rssi) {
                nets[found].rssi = rssi;
            }
            continue;
        }
        if (num >= max) {
            continue;
        }
        strlcpy(nets[num].ssid, ssid.c_str(), sizeof(nets[num].ssid));
        nets[num].rssi = rssi;
#if defined(ESP8266)
        nets[num].secure = (WiFi.encryptionType(ix) != ENC_TYPE_NONE);
#else
        nets[num].secure = (WiFi.encryptionType(ix) != WIFI_AUTH_OPEN);
#endif
        num++;
    }
    WiFi.scanDelete();

    // Sort by signal, strongest first
    for (int ix = 1; ix < num; ix++) {
        const WifiNet net = nets[ix];
        int jx = ix - 1;
        while ((jx >= 0) && (nets[jx].rssi < net.rssi)) {
            nets[jx + 1] = nets[jx];
            jx--;
        }
        nets[jx + 1] = net;
    }
    return num;
}

void wifiResume()
{
    if (state == WifiState::IDLE) {
        startRound();
    }
}
