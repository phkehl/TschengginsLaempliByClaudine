// Backend client: WebSocket connection to the backend
//
// Connects to <url>/ws/device?device=..&fw=..&ssid=..&ip=..&rssi=..&leds=..&app=laempli (WebSocket, one connection in both
// directions; one TLS connection with HTTPS). backendStep() does one step (connect, read the data that is there, wait for retry). ESP32: a FreeRTOS task runs it
// each 20 ms, because TLS handshakes block. ESP8266: the Arduino loop runs it (backendUpdate()); the LED animations run
// at yield() and delay() (scheduled functions), so they continue while a request blocks.
//
// - WebSocket: own client on the WiFiClient (handshake, frames), see wsHandshake() and WsParser. Text messages are
//   JSON {"event": <name>, "data": {...}}. The server pings, the client answers.
// - Basic auth, if a user is set
// - HTTPS: certificate check with the CA certificates (made from Debian ca-certificates at build time, see
//   tools/gen_ca_bundle.py), or no check if the insecure flag is set. If the check fails, the state is
//   CERT_UNTRUSTED until the settings change (user accepts insecure mode, or changes the URL).
//   ESP32: mbedTLS with the CA bundle. ESP8266: BearSSL with castore8266.cpp, receive buffer BACKEND_TLS_RX (the
//   server must not send larger TLS records), time for the certificate dates from NTP (see tlsTime()). The
//   connections (WebSocket, OTA download) use one TLS session (session ID resumption, see tlsSession): only the
//   first handshake has the certificate check and the key exchange, the others need less heap and time. The TLS
//   buffers go into the IRAM heap (MMU option in platformio.ini).
// - HTTPS cipher suites: ECDHE-RSA first, then the ECDSA ones (see tlsSuites()). A server with an RSA and an ECDSA
//   certificate (and SSLHonorCipherOrder off) then sends the RSA chain. RSA checks are much faster: measured
//   2026-11-11 on the ESP32 (Stägge), handshake 3.9 s with the ECDSA chain (3 x P-384 verify, 770 ms each), 0.7 s
//   with an RSA chain. A server with one certificate type works as before.
// - No data for BACKEND_TIMEOUT_MS (keepalive is 15 s): reconnect
// - Retry delay from BACKEND_RETRY_MIN_MS, doubled after each failure, up to BACKEND_RETRY_MAX_MS
// - A settings change or backendReconnect() reconnects at once
// - Only a Lämpli backend: "app=laempli" in the request (the backend checks it and the device type), and the device
//   takes nothing before a hello with "server": "laempli" (BACKEND_SERVER). The energy monitor server
//   (EnergyMonByClaudine) talks a similar protocol: its hello closes the connection (close code 1008), state "wrong
//   server: <name>", the next try waits BACKEND_RETRY_MAX_MS.
//
// Events (data is JSON):
// - "hello": time, UTC offset and heartbeat interval
// - "time": time and UTC offset (heartbeat, each 15 s)
// - "state": version and state: one entry per LED (null = unused) with label, path (source, names), status (stopped,
//   running, waiting, unknown), result (pass, fail, warn, unknown), link, time, info ({name: text}) and sounds
//   ({event: sound}, optional). See backend/laempli/leds.py and jobs.py. A new state can play a sound (a job
//   stopped), see jobsound.cpp.
// - "settings": seq and settings from the web page ({key: value}, only changed ones), see applySettings(). Bad values
//   are ignored. The device saves them and reports at once.
// - "update": firmware info, button on the web page: install it (see below)
// - "sound": sound name, button on the web page: play it
// - "led": LED test, buttons on the web page (mode, first, count), see ledremote.cpp
// - "ota": error text of a bad firmware part request: stops the update
//
// No data for heartbeat + BACKEND_MARGIN_MS: status "stale" (the UI blinks the time).
//
// Report: after hello, each "time" event (heartbeat), each change of the synced settings (settingsRev()) and each
// "settings" event, a "report" event with {device, rssi, uptime, heap, settings, ack} ("ack": seq of the last applied
// "settings" event, 0 after start). The first report of a connection also has "options" (e.g. LED brightness options,
// LED animations and melodies, for the web page; "no_ota": true without firmware update), "cpu" (chip name, see cpuName()) and "heap_size" [bytes]. While an update runs, and after it failed, the report has "ota"
// (see otaJson()). While an LED test runs, it has "led_test"; the start and the end of a test also send a report.
// Errors are only logged.
//
// Synced settings (device name and the settings of the firmware, see syncsettings.hpp): the device has the values,
// the backend has a copy (from the reports) and the changes from the web page (pending until acknowledged).
//
// Firmware update (OTA): the hello event has the info of the newest firmware for this device type (FW_NAME). If its
// timestamp is newer than FW_TIME, the state is AVAILABLE and the UI asks the user. backendOtaStart() starts the update
// on the open WebSocket connection (no second TLS connection, thus less heap): the device asks for the file in parts
// of BACKEND_OTA_CHUNK bytes ("ota" event with file, offset, length, and the bytes written so far), up to
// BACKEND_OTA_WINDOW parts ahead. The backend answers each with a binary message (offset, 4 bytes big endian, then the
// data). The parts go into the other app partition. At the end: MD5 check, "ota" event "done", restart. On an error:
// "ota" event "failed" with the error text. The backend shows the progress on the web page. A part must fit into the
// message buffer (BACKEND_DATA_MAX). On the ESP8266, the TLS records of the window must fit into the TLS receive
// buffer (BACKEND_TLS_RX). See backend/laempli/wsserver.py.
//
// Rollback (ESP32 only): verifyRollbackLater() returns true, so a new firmware is valid only after its first hello
// from the backend. If it restarts before that, the bootloader goes back to the old firmware.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <base64.h>
#include <fw_version.h>

#if defined(ESP8266)
#include <ESP8266HTTPClient.h>
#include <ESP8266WiFi.h>
#include <Updater.h>
#include <WiFiClientSecure.h>
#include <bearssl/bearssl_hash.h>
#include <time.h>
#else
#include <HTTPClient.h>
#include <Update.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_ota_ops.h>
#include <esp_random.h>
#include <mbedtls/sha1.h>
#endif

#include <memory>
#include <new>

#include "backend.hpp"
#include "compat.hpp"
#include "config.hpp"
#include "device.hpp"
#include "jobsound.hpp"
#include "ledremote.hpp"
#include "settings.hpp"
#include "syncsettings.hpp"

#if defined(ESP8266)
#include "castore8266.hpp"

static CaStore caStore;
#else
#include <mbedtls/ssl.h>
#include <mbedtls/ssl_ciphersuites.h>

// CA bundle, embedded by platformio.ini
extern const uint8_t caBundle[] asm("_binary__pio_ca_bundle_x509_crt_bundle_bin_start");

// TLS errors for an untrusted certificate. With the CA bundle, mbedTLS reports -0x3000.
static constexpr int MBEDTLS_CERT_VERIFY_FAILED = -0x2700;  // MBEDTLS_ERR_X509_CERT_VERIFY_FAILED
static constexpr int MBEDTLS_X509_FATAL = -0x3000;          // MBEDTLS_ERR_X509_FATAL_ERROR (verify callback)
#endif

static Mutex mtx;
static BackendStatus status = { BackendState::NONE, "", 0, 0, true, 0, 0, 0 };
static LedInfo ledInfos[LED_NUM];        // From last state
static bool ledsValid = false;           // State received since start
static uint32_t lastDataMs = 0;          // Last data from backend
static uint32_t heartbeatMs = 15000;     // From hello event
static bool timeValid = false;
static int64_t timeLocalMs = 0;          // Local time [ms] at timeMs
static int64_t timeUnixMs = 0;           // Unix time [ms] at timeMs
static uint32_t timeMs = 0;

static OtaStatus ota = { OtaState::NONE, "", 0, "" };
static char otaMd5[33] = "";
static char otaFile[96] = "";
static size_t otaSize = 0;
static bool otaAsked = false;
static Atomic<bool> otaStart(false);
static bool reportDue = false;  // Heartbeat received, send report
static char* msgData = nullptr;  // Message text, on the heap (see allocData())
static uint32_t settingsAck = 0;  // Seq of last applied settings event

static Atomic<uint32_t> reconnectCnt(0);
static uint32_t certVersion = 0;  // Settings version of untrusted cert
static bool certAsked = false;

// The protocol and server name this firmware talks to
static constexpr const char* BACKEND_SERVER = "laempli";
static bool helloOk = false;     // Hello of a Lämpli backend seen
static char badServer[24] = "";  // Server name of a wrong hello

// Result of one stream step or connection
enum class Result
{
    OPEN,     // Stream is open
    ERROR,
    CERT,
    CHANGED,
};

/**
 * @brief Set state and error text
 */
static void setState(const BackendState state, const char* error = "")
{
    Lock lock(mtx);
    static const char* const names[] = { "not set", "waiting for WiFi", "connecting", "connected", "error",
        "certificate not trusted" };
    if ((status.state != state) || (strcmp(status.error, error) != 0)) {
        Serial.printf("Backend: %s %s\r\n", names[(int)state], error);
    }
    status.state = state;
    strlcpy(status.error, error, sizeof(status.error));
}

/**
 * @brief Check for settings change or reconnect request
 */
static bool isChanged(const uint32_t settingsVer, const uint32_t reconnectVer)
{
    BackendCfg cfg;
    return ((settingsGetBackend(cfg) != settingsVer) || (reconnectCnt.load() != reconnectVer));
}

#if !defined(ESP8266)
/**
 * @brief Arduino core: mark app valid later
 */
extern "C" bool verifyRollbackLater()
{
    return true;
}
#endif

/**
 * @brief Mark running firmware as valid
 */
static void markValid()
{
#if !defined(ESP8266)
    esp_ota_img_states_t imgState;
    if ((esp_ota_get_state_partition(esp_ota_get_running_partition(), &imgState) == ESP_OK) &&
        (imgState == ESP_OTA_IMG_PENDING_VERIFY)) {
        esp_ota_mark_app_valid_cancel_rollback();
        Serial.println("OTA: firmware marked valid");
    }
#endif
}

/**
 * @brief Check firmware info of hello
 */
static void checkFirmware(JsonVariantConst fw)
{
    Lock lock(mtx);
    if ((ota.state == OtaState::RUNNING) || (ota.state == OtaState::DONE)) {
        return;
    }
    const char* version = fw["version"] | "";
    const char* time = fw["time"] | "";
    const char* md5 = fw["md5"] | "";
    const char* file = fw["file"] | "";
    const size_t size = fw["size"] | 0;
    const bool newer = ((strlen(time) == 14) && (strcmp(time, FW_TIME) > 0) && (strlen(md5) == 32) && (size > 0) &&
                        (file[0] != '\0') && (strlen(file) < sizeof(otaFile)));
    if (!newer) {
        ota.state = OtaState::NONE;
        return;
    }
    if (strcmp(ota.version, version) != 0) {
        otaAsked = false;
        Serial.printf("OTA: firmware %s available\r\n", version);
    }
    if (ota.state != OtaState::FAILED) {
        ota.state = OtaState::AVAILABLE;
    }
    strlcpy(ota.version, version, sizeof(ota.version));
    strlcpy(otaMd5, md5, sizeof(otaMd5));
    strlcpy(otaFile, file, sizeof(otaFile));
    otaSize = size;
}

/**
 * @brief Copy text for the display
 *
 * Leaves out chars the display font does not have: 4-byte UTF-8 chars (e.g. emoji) and variation selectors. Removes
 * leading spaces.
 *
 * @param[out] dst   Destination
 * @param[in]  src   Source text (UTF-8)
 * @param[in]  size  Size of destination
 */
static void copyText(char* dst, const char* src, const size_t size)
{
    size_t len = 0;
    const uint8_t* in = (const uint8_t*)src;
    while ((*in != 0) && (len < (size - 1))) {
        if (*in >= 0xf0) {
            in++;
            while ((*in & 0xc0) == 0x80) {
                in++;
            }
            continue;
        }
        if ((in[0] == 0xef) && (in[1] == 0xb8) && (in[2] >= 0x80) && (in[2] <= 0x8f)) {
            in += 3;
            continue;
        }
        if ((len == 0) && (*in == ' ')) {
            in++;
            continue;
        }
        dst[len] = (char)*in;
        len++;
        in++;
    }
    dst[len] = '\0';
}

/**
 * @brief Parse LED states of a state event
 *
 * @param[in]  json  The "leds" array (null entries: unused LED)
 * @param[out] leds  LED states
 * @param[in]  num   Number of LEDs
 *
 * @returns the number of used LEDs
 */
static int parseLeds(JsonVariantConst json, LedInfo* leds, const int num)
{
    int used = 0;
    for (int ix = 0; ix < num; ix++) {
        LedInfo& led = leds[ix];
        memset(&led, 0, sizeof(led));
        JsonVariantConst item = json[ix];
        if (!item.is<JsonObjectConst>()) {
            continue;
        }
        led.used = true;
        used++;
        jobSoundParse(item["sounds"], led.sounds);
        // Ignore case, older backends send lowercase
        const char* result = item["result"] | "";
        led.result = LedResult::UNKNOWN;
        for (const LedResult value : { LedResult::PASS, LedResult::FAIL, LedResult::WARN }) {
            if (strcasecmp(result, ledResultName(value)) == 0) {
                led.result = value;
            }
        }
        const char* st = item["status"] | "";
        led.status = LedStatus::UNKNOWN;
        for (const LedStatus value : { LedStatus::STOPPED, LedStatus::RUNNING, LedStatus::WAITING }) {
            if (strcasecmp(st, ledStatusName(value)) == 0) {
                led.status = value;
            }
        }
        copyText(led.label, item["label"] | "", sizeof(led.label));
        JsonArrayConst path = item["path"];
        copyText(led.source, path[0] | "", sizeof(led.source));
        for (size_t pix = 1; pix < path.size(); pix++) {
            if (pix > 1) {
                strlcat(led.path, " / ", sizeof(led.path));
            }
            strlcat(led.path, path[pix] | "", sizeof(led.path));
        }
        led.time = item["time"] | 0.0;
        JsonObjectConst info = item["info"];
        copyText(led.detail, info["status"] | "", sizeof(led.detail));
        for (JsonPairConst kv : info) {
            if (led.numInfo >= LED_INFO_MAX) {
                break;
            }
            if (strcmp(kv.key().c_str(), "status") == 0) {
                continue;
            }
            LedInfoLine& line = led.info[led.numInfo];
            copyText(line.name, kv.key().c_str(), sizeof(line.name));
            copyText(line.value, kv.value() | "", sizeof(line.value));
            led.numInfo++;
        }
    }
    return used;
}

/**
 * @brief Apply settings from the backend
 *
 * Keys: name, and the keys of the firmware (syncApplySetting()). Bad values are ignored (the next report has the real
 * values).
 */
static void applySettings(JsonObjectConst values)
{
    for (JsonPairConst kv : values) {
        const char* key = kv.key().c_str();
        JsonVariantConst val = kv.value();
        bool ok = false;
        if (strcmp(key, "name") == 0) {
            ok = (val.is<const char*>() && settingsSetName(val.as<const char*>()));
        } else {
            ok = syncApplySetting(key, val);
        }
        Serial.printf("Backend: setting %s %s\r\n", key, (ok ? "set" : "ignored"));
    }
}

/**
 * @brief Add synced settings to JSON
 */
static void addSettings(JsonObject obj)
{
    char name[DEVICE_NAME_MAX + 1];
    settingsGetName(name, sizeof(name));
    obj["name"] = name;
    syncAddSettings(obj);
}

// Firmware transfer on the WebSocket (backend task only)
struct OtaRun
{
    bool active = false;
    char file[96] = "";
    size_t size = 0;
    size_t done = 0;         // Bytes written
    size_t asked = 0;        // Bytes requested
    uint32_t lastMs = 0;     // Last data
    char error[48] = "";     // Error of a part or an event
};
static OtaRun otaRun;

// Part with offset must fit into msgData
static_assert((BACKEND_OTA_CHUNK + 4) < BACKEND_DATA_MAX, "BACKEND_OTA_CHUNK too large");

/**
 * @brief Set OTA state
 */
static void setOta(const OtaState state, const int progress, const char* error = "")
{
    Lock lock(mtx);
    ota.state = state;
    ota.progress = progress;
    strlcpy(ota.error, error, sizeof(ota.error));
}

/**
 * @brief Get updater error text
 */
static void updateError(char* error, const size_t size)
{
#if defined(ESP8266)
    strlcpy(error, Update.getErrorString().c_str(), size);
#else
    strlcpy(error, Update.errorString(), size);
#endif
}

/**
 * @brief Handle firmware part (binary message)
 *
 * Data: offset (4 bytes, big endian), then the part. Writes the part. Errors go to otaRun.error, see otaPoll().
 */
static void handleChunk(const uint8_t* data, const size_t len)
{
    if ((!otaRun.active) || (otaRun.error[0] != '\0')) {
        return;
    }
    if (len <= 4) {
        strlcpy(otaRun.error, "bad part", sizeof(otaRun.error));
        return;
    }
    const size_t offset = (((size_t)data[0] << 24) | ((size_t)data[1] << 16) | ((size_t)data[2] << 8) | data[3]);
    const size_t num = (len - 4);
    if ((offset != otaRun.done) || ((otaRun.done + num) > otaRun.asked)) {
        snprintf(otaRun.error, sizeof(otaRun.error), "bad part at %u", (unsigned)offset);
        return;
    }
    if (Update.write((uint8_t*)&data[4], num) != num) {
        updateError(otaRun.error, sizeof(otaRun.error));
        return;
    }
    otaRun.done += num;
    otaRun.lastMs = millis();
    setOta(OtaState::RUNNING, (int)(((uint64_t)otaRun.done * 100) / otaRun.size));
}

/**
 * @brief Handle one event (WebSocket message)
 */
static void handleEvent(const char* event, JsonVariantConst data)
{
    // Only from a Lämpli backend, nothing before its hello
    if (badServer[0] != '\0') {
        return;
    }
    if (strcmp(event, "hello") == 0) {
        const char* server = data["server"] | "";
        if (strcmp(server, BACKEND_SERVER) != 0) {
            strlcpy(badServer, ((server[0] != '\0') ? server : "unknown"), sizeof(badServer));
            Serial.printf("Backend: wrong server \"%s\", not %s\r\n", badServer, BACKEND_SERVER);
            return;
        }
        helloOk = true;
    } else if (!helloOk) {
        Serial.printf("Backend: event %s before hello ignored\r\n", event);
        return;
    }
    if ((strcmp(event, "hello") == 0) || (strcmp(event, "time") == 0)) {
        Lock lock(mtx);
        if (data["time"].is<double>()) {
            timeUnixMs = (int64_t)(data["time"].as<double>() * 1000.0);
            timeLocalMs = timeUnixMs + ((int64_t)(data["utc_offset"] | 0) * 1000);
            timeMs = millis();
            timeValid = true;
        }
        if (data["heartbeat"].is<int>()) {
            heartbeatMs = (uint32_t)data["heartbeat"].as<int>() * 1000;
        }
        reportDue = true;
    }
    if (strcmp(event, "hello") == 0) {
        Serial.print("Backend: hello ");
        serializeJson(data, Serial);
        Serial.print("\r\n");
        checkFirmware(data["firmware"]);
        markValid();
    } else if (strcmp(event, "state") == 0) {
        const uint32_t version = data["version"] | 0;
        // New state: on the heap only for this event
        std::unique_ptr<LedInfo[]> infos(new (std::nothrow) LedInfo[LED_NUM]);
        if (!infos) {
            Serial.println("Backend: no memory for state");
            return;
        }
        const int used = parseLeds(data["state"]["leds"], infos.get(), LED_NUM);
        heapSample();
        int8_t sound = SOUND_NONE;
        {
            Lock lock(mtx);
            // Count changes (onboard LED flashes), sound: not the first state
            if (ledsValid) {
                sound = jobSoundSelect(ledInfos, infos.get(), LED_NUM);
                bool changed = false;
                for (int ix = 0; ix < LED_NUM; ix++) {
                    changed = (changed || (infos[ix].used != ledInfos[ix].used) ||
                        (infos[ix].status != ledInfos[ix].status) || (infos[ix].result != ledInfos[ix].result));
                }
                if (changed) {
                    status.changes++;
                } else if (memcmp(infos.get(), ledInfos, sizeof(ledInfos)) != 0) {
                    status.updates++;
                }
            }
            status.version = version;
            status.leds = used;
            memcpy(ledInfos, infos.get(), sizeof(ledInfos));
            ledsValid = true;
        }
        Serial.printf("Backend: state version %u, %d LEDs\r\n", (unsigned)version, used);
        jobSoundPlay(sound);
    } else if (strcmp(event, "settings") == 0) {
        if (!data["settings"].is<JsonObjectConst>()) {
            Serial.println("Backend: bad settings event");
            return;
        }
        applySettings(data["settings"].as<JsonObjectConst>());
        settingsAck = data["seq"] | 0;
        reportDue = true;
        {
            Lock lock(mtx);
            status.settingsApplied++;
        }
    } else if (strcmp(event, "update") == 0) {
        // Update button on the web page
        checkFirmware(data["firmware"]);
        Serial.println("Backend: firmware update from web page");
        backendOtaStart();
    } else if (strcmp(event, "sound") == 0) {
        // Sound test on the web page
        jobSoundPlay(jobSoundFind(data["sound"] | ""));
    } else if (strcmp(event, "led") == 0) {
        // LED test on the web page
        if (!ledRemoteSet(data)) {
            Serial.println("Backend: bad led event");
        }
    } else if ((strcmp(event, "ota") == 0) && otaRun.active && (otaRun.error[0] == '\0')) {
        // Bad part request
        strlcpy(otaRun.error, (data["error"] | "backend error"), sizeof(otaRun.error));
    }
}

/**
 * @brief Make JSON filter for the messages
 *
 * Keeps all but the LED fields that the firmware does not use (link; info lines if LED_INFO_MAX is 0, but the status
 * text). Less memory for many LEDs.
 *
 * @returns the filter
 */
static JsonDocument makeFilter()
{
    JsonDocument filter;
    filter["event"] = true;
    filter["data"]["*"] = true;
    JsonObject led = filter["data"]["state"]["leds"].add<JsonObject>();
    for (const char* key : { "label", "path", "status", "result", "time", "sounds" }) {
        led[key] = true;
    }
    if (LED_INFO_MAX > 0) {
        led["info"] = true;
    } else {
        led["info"]["status"] = true;
    }
    return filter;
}

/**
 * @brief Parse and handle one WebSocket message: {"event": <name>, "data": {...}}
 */
static void handleMessage(const char* text)
{
    static const JsonDocument filter = makeFilter();
    JsonDocument doc;
    const DeserializationError err = deserializeJson(doc, text, DeserializationOption::Filter(filter));
    if (err || !doc["event"].is<const char*>()) {
        Serial.printf("Backend: bad message: %s\r\n", (err ? err.c_str() : "no event"));
        return;
    }
    heapSample();
    handleEvent(doc["event"].as<const char*>(), doc["data"].as<JsonVariantConst>());
}

// WebSocket opcodes
static constexpr uint8_t WS_CONT = 0x0;
static constexpr uint8_t WS_TEXT = 0x1;
static constexpr uint8_t WS_BINARY = 0x2;
static constexpr uint8_t WS_CLOSE = 0x8;
static constexpr uint8_t WS_PING = 0x9;
static constexpr uint8_t WS_PONG = 0xa;

// WebSocket frame parser: reads the stream byte by byte. Text and binary messages (also in more frames) go into
// msgData, the payload of control frames (ping, close) into ctrl. Binary messages are firmware parts (handleChunk()).
// The server does not mask.
struct WsParser
{
    uint8_t head[14];        // Frame header
    size_t headLen = 0;
    size_t headNeed = 2;     // Header bytes needed
    uint8_t opcode = 0;      // Opcode of this frame
    bool fin = false;        // Last frame of the message
    bool inPayload = false;
    uint64_t left = 0;       // Payload bytes left
    bool skip = false;       // Skip this payload
    bool msgOn = false;      // Message started, more frames follow
    bool binary = false;     // Binary message
    size_t msgLen = 0;
    bool overflow = false;
    uint8_t ctrl[125];       // Control frame payload
    size_t ctrlLen = 0;
    bool pongDue = false;    // Ping received: send pong with ctrl
    bool closed = false;     // Close frame or protocol error

    /**
     * @brief Handle one byte, run the message at its end
     */
    void feed(const uint8_t byte)
    {
        if (!inPayload) {
            head[headLen] = byte;
            headLen++;
            if (headLen == 2) {
                const uint8_t len7 = (head[1] & 0x7f);
                headNeed = 2 + ((len7 == 126) ? 2 : ((len7 == 127) ? 8 : 0)) + (((head[1] & 0x80) != 0) ? 4 : 0);
            }
            if (headLen < headNeed) {
                return;
            }
            startFrame();
            return;
        }
        if (!skip) {
            if (opcode >= WS_CLOSE) {
                if (ctrlLen < sizeof(ctrl)) {
                    ctrl[ctrlLen] = byte;
                    ctrlLen++;
                }
            } else if (msgLen < (BACKEND_DATA_MAX - 1)) {
                msgData[msgLen] = (char)byte;
                msgLen++;
            } else {
                overflow = true;
            }
        }
        left--;
        if (left == 0) {
            inPayload = false;
            endFrame();
        }
    }

    /**
     * @brief Frame header complete: payload length, what to do with the payload
     */
    void startFrame()
    {
        fin = ((head[0] & 0x80) != 0);
        opcode = (head[0] & 0x0f);
        const uint8_t len7 = (head[1] & 0x7f);
        if (len7 == 126) {
            left = ((uint64_t)head[2] << 8) | head[3];
        } else if (len7 == 127) {
            left = 0;
            for (int ix = 2; ix < 10; ix++) {
                left = (left << 8) | head[ix];
            }
        } else {
            left = len7;
        }
        headLen = 0;
        headNeed = 2;
        // Masked frame from the server: not allowed
        if ((head[1] & 0x80) != 0) {
            closed = true;
        }
        skip = false;
        if (opcode >= WS_CLOSE) {
            ctrlLen = 0;
        } else if ((opcode == WS_TEXT) || (opcode == WS_BINARY)) {
            msgOn = true;
            binary = (opcode == WS_BINARY);
            msgLen = 0;
            overflow = false;
        } else if ((opcode != WS_CONT) || !msgOn) {
            skip = true;
        }
        if (left == 0) {
            endFrame();
        } else {
            inPayload = true;
        }
    }

    /**
     * @brief Frame complete: message, ping, close
     */
    void endFrame()
    {
        if (opcode == WS_PING) {
            pongDue = true;
        } else if (opcode == WS_CLOSE) {
            closed = true;
        } else if (((opcode == WS_TEXT) || (opcode == WS_BINARY) || (opcode == WS_CONT)) && msgOn && fin) {
            msgOn = false;
            if (overflow) {
                Serial.println("Backend: message too large");
            } else if (binary) {
                handleChunk((const uint8_t*)msgData, msgLen);
            } else {
                msgData[msgLen] = '\0';
                handleMessage(msgData);
            }
        }
    }
};

// One connection: plain or TLS
struct Conn
{
    WiFiClient plain;
#if defined(ESP8266)
    BearSSL::WiFiClientSecure secure;
#else
    WiFiClientSecure secure;
#endif
};

// Open WebSocket connection
struct Connection
{
    Conn conn;
    WiFiClient* client = nullptr;
    bool repFailed = false;
    uint32_t repRev = 0;      // Settings counter of last report
    uint32_t repLedRev = 0;   // LED test counter of last report
    bool repOptions = false;  // Options sent
    WsParser parser;
    uint32_t lastData = 0;
    uint32_t settingsVer = 0;
    uint32_t reconnectVer = 0;
};

// Parts of the backend URL
struct UrlParts
{
    bool https = false;
    char host[64] = "";
    uint16_t port = 80;
    char path[96] = "";  // Without trailing slash, "" for none
};

/**
 * @brief Split backend URL: http(s)://host[:port][/path]
 *
 * @returns false if not a valid URL
 */
static bool parseUrl(const char* url, UrlParts& parts)
{
    const char* rest = nullptr;
    if (strncmp(url, "https://", 8) == 0) {
        parts.https = true;
        parts.port = 443;
        rest = url + 8;
    } else if (strncmp(url, "http://", 7) == 0) {
        rest = url + 7;
    } else {
        return false;
    }
    const size_t hostLen = strcspn(rest, ":/");
    if ((hostLen == 0) || (hostLen >= sizeof(parts.host))) {
        return false;
    }
    memcpy(parts.host, rest, hostLen);
    parts.host[hostLen] = '\0';
    rest += hostLen;
    if (*rest == ':') {
        char* end = nullptr;
        const long port = strtol(rest + 1, &end, 10);
        if ((end == (rest + 1)) || (port < 1) || (port > 65535)) {
            return false;
        }
        parts.port = (uint16_t)port;
        rest = end;
    }
    if (((*rest != '\0') && (*rest != '/')) || (strlen(rest) >= sizeof(parts.path))) {
        return false;
    }
    strlcpy(parts.path, rest, sizeof(parts.path));
    size_t len = strlen(parts.path);
    while ((len > 0) && (parts.path[len - 1] == '/')) {
        len--;
        parts.path[len] = '\0';
    }
    return true;
}

/**
 * @brief Get a random 32 bit value (hardware RNG)
 */
static uint32_t randomWord()
{
#if defined(ESP8266)
    return RANDOM_REG32;
#else
    return esp_random();
#endif
}

/**
 * @brief Get the Sec-WebSocket-Accept value for a key: base64(SHA-1(key + GUID))
 */
static String wsAccept(const String& key)
{
    const String text = key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
    uint8_t hash[20];
#if defined(ESP8266)
    br_sha1_context ctx;
    br_sha1_init(&ctx);
    br_sha1_update(&ctx, text.c_str(), text.length());
    br_sha1_out(&ctx, hash);
#else
    mbedtls_sha1_ret((const unsigned char*)text.c_str(), text.length(), hash);
#endif
    return base64::encode(hash, sizeof(hash));
}

/**
 * @brief Send one WebSocket frame (masked, as a client must), one write
 *
 * @returns true if sent
 */
static bool wsSend(WiFiClient& client, const uint8_t opcode, const uint8_t* data, const size_t len)
{
    if (len > 0xffff) {
        return false;
    }
    std::unique_ptr<uint8_t[]> buf(new (std::nothrow) uint8_t[len + 8]);
    if (!buf) {
        return false;
    }
    size_t pos = 0;
    buf[pos++] = (0x80 | opcode);
    if (len < 126) {
        buf[pos++] = (0x80 | len);
    } else {
        buf[pos++] = (0x80 | 126);
        buf[pos++] = (len >> 8);
        buf[pos++] = (len & 0xff);
    }
    const uint32_t key = randomWord();
    const uint8_t* mask = &buf[pos];
    memcpy(&buf[pos], &key, 4);
    pos += 4;
    for (size_t ix = 0; ix < len; ix++) {
        buf[pos + ix] = (data[ix] ^ mask[ix & 3]);
    }
    return (client.write(buf.get(), pos + len) == (pos + len));
}

static std::unique_ptr<Connection> stream;  // Open stream, nullptr if none
static uint32_t retryMs = BACKEND_RETRY_MIN_MS;
static bool waiting = false;            // Waiting before next connect
static uint32_t waitStart = 0;
static uint32_t waitMs = 0;
static uint32_t waitSettingsVer = 0;
static uint32_t waitReconnectVer = 0;

#if defined(ESP8266)
static BearSSL::Session tlsSession;     // TLS session of both connections
static char tlsSessionUrl[128] = "";    // URL of the session
static bool tlsSessionInsecure = false;

/**
 * @brief Use TLS session for a connection
 *
 * A new session after a change of the URL or the insecure flag: a session without certificate check must not be
 * used with the check.
 */
static void tlsUseSession(BearSSL::WiFiClientSecure& secure, const BackendCfg& cfg)
{
    if ((strcmp(tlsSessionUrl, cfg.url) != 0) || (tlsSessionInsecure != cfg.insecure)) {
        tlsSession = BearSSL::Session();
        strlcpy(tlsSessionUrl, cfg.url, sizeof(tlsSessionUrl));
        tlsSessionInsecure = cfg.insecure;
    }
    secure.setSession(&tlsSession);
}

static bool tlsTimeGuess = false;  // Time of the check is the build time

/**
 * @brief Set time for the certificate check
 *
 * BearSSL checks the certificate dates. Time from NTP (configTime(), see backendInit()), else the time from the
 * backend (last connection), else the build time of the firmware. With the build time, a certificate newer than the
 * firmware is "not yet valid": connError() then retries (NTP has the time later), see tlsTimeGuess.
 */
static void tlsTime(BearSSL::WiFiClientSecure& secure)
{
    time_t now = time(nullptr);
    tlsTimeGuess = false;
    if (now < 1700000000) {
        int64_t unixMs = 0;
        if (backendGetUnixMs(unixMs)) {
            now = (time_t)(unixMs / 1000);
        } else {
            struct tm tm = {};
            sscanf(FW_TIME, "%4d%2d%2d%2d%2d%2d", &tm.tm_year, &tm.tm_mon, &tm.tm_mday, &tm.tm_hour, &tm.tm_min,
                &tm.tm_sec);
            tm.tm_year -= 1900;
            tm.tm_mon -= 1;
            now = mktime(&tm);
            tlsTimeGuess = true;
        }
    }
    secure.setX509Time(now);
}

// Cipher suites: ECDHE-RSA first, then ECDHE-ECDSA
static const uint16_t tlsSuites[] PROGMEM = {
    BR_TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256,
    BR_TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,
    BR_TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,
    BR_TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA256,
    BR_TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA384,
    BR_TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256,
    BR_TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,
    BR_TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384,
    BR_TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA256,
    BR_TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA384,
};
#else
/**
 * @brief Cipher suites: ECDHE-RSA first, then the others
 *
 * The mbedTLS default list (in its order), with the ECDHE-RSA suites moved to the front.
 *
 * @returns zero-terminated list of suite IDs
 */
static const int* tlsSuites()
{
    static int* list = nullptr;
    if (list == nullptr) {
        const int* all = mbedtls_ssl_list_ciphersuites();
        size_t num = 0;
        while (all[num] != 0) {
            num++;
        }
        list = new int[num + 1];
        size_t pos = 0;
        for (int pass = 0; pass < 2; pass++) {
            for (size_t ix = 0; ix < num; ix++) {
                const mbedtls_ssl_ciphersuite_t* info = mbedtls_ssl_ciphersuite_from_id(all[ix]);
                const bool rsa = ((info != nullptr) && (info->key_exchange == MBEDTLS_KEY_EXCHANGE_ECDHE_RSA));
                if (rsa == (pass == 0)) {
                    list[pos++] = all[ix];
                }
            }
        }
        list[pos] = 0;
    }
    return list;
}

// WiFiClientSecure has no setting for the cipher suites. It calls arduino_esp_crt_bundle_attach() with the mbedTLS
// config before the handshake (with the CA bundle): the linker option --wrap (platformio.ini) sends this call here.
extern "C" esp_err_t __real_arduino_esp_crt_bundle_attach(void* conf);

/**
 * @brief Set our cipher suites, then attach bundle
 */
extern "C" esp_err_t __wrap_arduino_esp_crt_bundle_attach(void* conf)
{
    mbedtls_ssl_conf_ciphersuites((mbedtls_ssl_config*)conf, tlsSuites());
    return __real_arduino_esp_crt_bundle_attach(conf);
}
#endif

/**
 * @brief Set up the client of a connection: plain, or TLS with the certificate check (or insecure)
 *
 * @returns the client to use
 */
static WiFiClient* setupClient(Conn& conn, const BackendCfg& cfg)
{
    if (strncmp(cfg.url, "https://", 8) != 0) {
        return &conn.plain;
    }
#if defined(ESP8266)
    conn.secure.setBufferSizes(BACKEND_TLS_RX, BACKEND_TLS_TX);
    conn.secure.setCiphers(tlsSuites, (sizeof(tlsSuites) / sizeof(tlsSuites[0])));
    tlsUseSession(conn.secure, cfg);
    if (cfg.insecure) {
        conn.secure.setInsecure();
    } else {
        conn.secure.setCertStore(&caStore);
        tlsTime(conn.secure);
    }
#else
    if (cfg.insecure) {
        conn.secure.setInsecure();
    } else {
        conn.secure.setCACertBundle(caBundle);
    }
#endif
    return &conn.secure;
}

#if !defined(ESP8266)
/**
 * @brief Idle watchdog of core 0 on or off (ESP32)
 *
 * The TLS handshake runs in the backend task on core 0. The certificate check does not yield for up to about 5 s
 * (measured 2026-10-28: connect 5.1 s), the limit of the task watchdog for the idle task of core 0. A bit more load
 * on the other core (e.g. one more task) then resets the ESP32. Thus the idle task of core 0 is not watched during
 * the TLS connect. The ESP8266 does not need it (its handshake loop yields).
 */
static void tlsWatchdog(const bool on)
{
    if (on) {
        enableCore0WDT();
    } else {
        disableCore0WDT();
    }
}
#endif

/**
 * @brief Connect and do the WebSocket handshake (GET with Upgrade, check of Sec-WebSocket-Accept)
 *
 * @returns 101 if open, another HTTP status, or < 0 (HTTPC_ERROR_*): no connection or bad answer
 */
static int wsHandshake(WiFiClient& client, const BackendCfg& cfg, const UrlParts& url, const String& path)
{
#if defined(ESP8266)
    client.setTimeout(10000);
    const uint32_t start = millis();
    if (!client.connect(url.host, url.port)) {
        return -1;
    }
    Serial.printf("Backend: connect took %lu ms\r\n", (unsigned long)(millis() - start));
    heapSample();
#else
    const uint32_t start = millis();
    tlsWatchdog(false);
    const bool connected = client.connect(url.host, url.port, 10000);
    tlsWatchdog(true);
    if (!connected) {
        return -1;
    }
    Serial.printf("Backend: connect took %lu ms\r\n", (unsigned long)(millis() - start));
#endif
    client.setTimeout(10000);

    uint8_t keyBytes[16];
    for (size_t ix = 0; ix < sizeof(keyBytes); ix += 4) {
        const uint32_t val = randomWord();
        memcpy(&keyBytes[ix], &val, 4);
    }
    const String key = base64::encode(keyBytes, sizeof(keyBytes));
    String req = "GET ";
    req += url.path;
    req += path;
    req += " HTTP/1.1\r\nHost: ";
    req += url.host;
    if (url.port != (url.https ? 443 : 80)) {
        req += ':';
        req += String(url.port);
    }
    req += "\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: ";
    req += key;
    req += "\r\nUser-Agent: laempli/";
    req += FW_VERSION;
    req += "\r\n";
    if (cfg.user[0] != '\0') {
        const String auth = String(cfg.user) + ":" + cfg.pass;
        req += "Authorization: Basic ";
        req += base64::encode((const uint8_t*)auth.c_str(), auth.length());
        req += "\r\n";
    }
    req += "\r\n";
    if (client.write((const uint8_t*)req.c_str(), req.length()) != req.length()) {
        return HTTPC_ERROR_SEND_HEADER_FAILED;
    }

    // Status line "HTTP/1.1 101 Switching Protocols", then the headers until an empty line
    String line = client.readStringUntil('\n');
    if (line.length() == 0) {
        return HTTPC_ERROR_READ_TIMEOUT;
    }
    const int space = line.indexOf(' ');
    const int code = ((space > 0) ? line.substring(space + 1).toInt() : 0);
    if (code <= 0) {
        return HTTPC_ERROR_NO_HTTP_SERVER;
    }
    const String expect = wsAccept(key);
    bool accepted = false;
    while (true) {
        line = client.readStringUntil('\n');
        line.trim();
        if (line.length() == 0) {
            break;
        }
        const int colon = line.indexOf(':');
        if ((colon > 0) && line.substring(0, colon).equalsIgnoreCase("Sec-WebSocket-Accept")) {
            String value = line.substring(colon + 1);
            value.trim();
            accepted = (value == expect);
        }
    }
    if ((code == 101) && !accepted) {
        return HTTPC_ERROR_NO_HTTP_SERVER;
    }
    return code;
}

/**
 * @brief Add OTA state to JSON (state, version, done, size, error)
 *
 * @returns false if no update runs, is done or failed (nothing added)
 */
static bool otaJson(JsonObject obj)
{
    // Not const: ArduinoJson copies char*, not const char*
    OtaStatus st = backendGetOta();
    if ((st.state != OtaState::RUNNING) && (st.state != OtaState::DONE) && (st.state != OtaState::FAILED)) {
        return false;
    }
    obj["state"] = ((st.state == OtaState::RUNNING) ? "running" : ((st.state == OtaState::DONE) ? "done" : "failed"));
    obj["version"] = st.version;
    obj["done"] = otaRun.done;
    obj["size"] = otaRun.size;
    if (st.state == OtaState::FAILED) {
        obj["error"] = st.error;
    }
    return true;
}

/**
 * @brief Send OTA event: state, and a part request if length > 0
 *
 * @returns true if sent
 */
static bool otaSend(Connection& st, const size_t offset = 0, const size_t length = 0)
{
    JsonDocument doc;
    doc["event"] = "ota";
    JsonObject data = doc["data"].to<JsonObject>();
    otaJson(data);
    if (length > 0) {
        data["file"] = otaRun.file;
        data["offset"] = offset;
        data["length"] = length;
    }
    String body;
    serializeJson(doc, body);
    return wsSend(*st.client, WS_TEXT, (const uint8_t*)body.c_str(), body.length());
}

/**
 * @brief Stop the update: abort the flash write, state FAILED
 */
static void otaStop(const char* error)
{
#if defined(ESP8266)
    // Not finished: no commit, only a reset
    Update.end(false);
#else
    if (Update.isRunning()) {
        Update.abort();
    }
#endif
    otaRun.active = false;
    Serial.printf("OTA: failed: %s\r\n", error);
    setOta(OtaState::FAILED, 0, error);
}

/**
 * @brief Start the update on the open connection
 */
static void otaBegin(Connection& st)
{
    char md5[33];
    {
        Lock lock(mtx);
        strlcpy(otaRun.file, otaFile, sizeof(otaRun.file));
        strlcpy(md5, otaMd5, sizeof(md5));
        otaRun.size = otaSize;
        Serial.printf("OTA: installing %s, %u bytes\r\n", ota.version, (unsigned)otaSize);
    }
    otaRun.done = 0;
    otaRun.asked = 0;
    otaRun.lastMs = millis();
    otaRun.error[0] = '\0';
    otaRun.active = true;
    setOta(OtaState::RUNNING, 0);
    if (!otaPossible()) {
        otaStop("no OTA partition, use USB");
        return;
    }
    if (!Update.begin(otaRun.size) || !Update.setMD5(md5)) {
        char error[48];
        updateError(error, sizeof(error));
        otaStop(error);
        otaSend(st);
    }
}

/**
 * @brief Run the update: ask for parts, check for errors and the end
 *
 * Restarts after a successful update.
 */
static void otaPoll(Connection& st)
{
    if (!otaRun.active) {
        return;
    }
    if ((otaRun.error[0] == '\0') && ((millis() - otaRun.lastMs) > BACKEND_OTA_TIMEOUT_MS)) {
        strlcpy(otaRun.error, "timeout", sizeof(otaRun.error));
    }
    // Ask for the next parts
    while ((otaRun.error[0] == '\0') && (otaRun.asked < otaRun.size) &&
           ((otaRun.asked - otaRun.done) < (BACKEND_OTA_CHUNK * BACKEND_OTA_WINDOW))) {
        const size_t len = min((size_t)BACKEND_OTA_CHUNK, (otaRun.size - otaRun.asked));
        if (!otaSend(st, otaRun.asked, len)) {
            strlcpy(otaRun.error, "send failed", sizeof(otaRun.error));
            break;
        }
        otaRun.asked += len;
    }
    if (otaRun.error[0] != '\0') {
        otaStop(otaRun.error);
        otaSend(st);
        return;
    }
    if (otaRun.done < otaRun.size) {
        return;
    }

    // All written: MD5 check, boot partition
    if (!Update.end()) {
        char error[48];
        updateError(error, sizeof(error));
        otaStop(error);
        otaSend(st);
        return;
    }
    otaRun.active = false;
    Serial.println("OTA: done, restarting");
    setOta(OtaState::DONE, 100);
    otaSend(st);
    const uint8_t code[2] = { 0x03, 0xe8 };  // 1000: normal closure
    wsSend(*st.client, WS_CLOSE, code, sizeof(code));
    delay(2000);
    ESP.restart();
}

/**
 * @brief Send report event
 */
static void sendReport(Connection& st)
{
    JsonDocument doc;
    doc["event"] = "report";
    JsonObject data = doc["data"].to<JsonObject>();
    data["device"] = deviceId();
    data["rssi"] = WiFi.RSSI();
    data["uptime"] = millis() / 1000;
    data["heap"] = ESP.getFreeHeap();
    st.repRev = settingsRev();
    addSettings(data["settings"].to<JsonObject>());
    data["ack"] = settingsAck;
    if (!otaJson(data["ota"].to<JsonObject>())) {
        data.remove("ota");
    }
    st.repLedRev = ledRemoteRev();
    if (!ledRemoteReport(data["led_test"].to<JsonObject>())) {
        data.remove("led_test");
    }
    if (!st.repOptions) {
        char cpu[64];
        cpuName(cpu, sizeof(cpu));
        data["cpu"] = cpu;
        data["heap_size"] = heapSize();
        JsonObject options = data["options"].to<JsonObject>();
        syncAddOptions(options);
        jobSoundOptions(options);
        ledRemoteOptions(options);
        if (!otaPossible()) {
            options["no_ota"] = true;
        }
    }
    String body;
    serializeJson(doc, body);
    heapSample();

    const bool failed = !wsSend(*st.client, WS_TEXT, (const uint8_t*)body.c_str(), body.length());
    st.repOptions = (st.repOptions || !failed);
    if (failed && !st.repFailed) {
        Serial.printf("Backend: report failed, heap %u free, %u largest block\r\n", (unsigned)ESP.getFreeHeap(),
            (unsigned)heapMaxBlock());
    }
    st.repFailed = failed;
}

/**
 * @brief Read the stream data that is there, send reports
 *
 * @returns OPEN, or why the stream ends
 */
static Result pollStream(Connection& st, const BackendCfg& cfg)
{
    if (isChanged(st.settingsVer, st.reconnectVer)) {
        return Result::CHANGED;
    }
    WiFiClient& client = *st.client;
    if (!client.connected() && (client.available() == 0)) {
        setState(BackendState::ERROR, "connection closed");
        return Result::ERROR;
    }
    if ((millis() - st.lastData) >= BACKEND_TIMEOUT_MS) {
        setState(BackendState::ERROR, "timeout");
        return Result::ERROR;
    }
    if (client.available() > 0) {
        st.lastData = millis();
        {
            Lock lock(mtx);
            lastDataMs = st.lastData;
        }
        uint8_t buf[256];
        int avail = client.available();
        while (avail > 0) {
            const int num = client.read(buf, min((size_t)avail, sizeof(buf)));
            if (num <= 0) {
                break;
            }
            for (int ix = 0; ix < num; ix++) {
                st.parser.feed(buf[ix]);
            }
            avail = client.available();
        }
    }
    WsParser& wsp = st.parser;
    if (wsp.pongDue) {
        wsp.pongDue = false;
        wsSend(client, WS_PONG, wsp.ctrl, wsp.ctrlLen);
    }
    if (wsp.closed) {
        setState(BackendState::ERROR, "connection closed");
        return Result::ERROR;
    }
    if (badServer[0] != '\0') {
        const uint8_t code[2] = { 0x03, 0xf0 };  // 1008: policy violation
        wsSend(client, WS_CLOSE, code, sizeof(code));
        char msg[48];
        snprintf(msg, sizeof(msg), "wrong server: %s", badServer);
        setState(BackendState::ERROR, msg);
        return Result::ERROR;
    }

    // Firmware update: start, ask for parts, end
    if (otaStart.exchange(false)) {
        otaBegin(st);
    }
    otaPoll(st);

    // Heartbeat or settings event; settings or LED test changed on the device: report at once
    if (reportDue || (settingsRev() != st.repRev) || (ledRemoteRev() != st.repLedRev)) {
        reportDue = false;
        sendReport(st);
    }
    return Result::OPEN;
}

/**
 * @brief URL-encode a string
 */
static String urlEncode(const char* str)
{
    static const char* const hex = "0123456789ABCDEF";
    String out;
    for (const char* ch = str; *ch != '\0'; ch++) {
        const uint8_t val = *ch;
        if (isalnum(val) || (val == '-') || (val == '_') || (val == '.') || (val == '~')) {
            out += (char)val;
        } else {
            out += '%';
            out += hex[val >> 4];
            out += hex[val & 0x0f];
        }
    }
    return out;
}

/**
 * @brief Get WebSocket path with device info
 */
static String devicePath()
{
    String path = "/ws/device?device=";
    path += urlEncode(deviceId());
    path += "&fw=";
    path += urlEncode(FW_VERSION);
    path += "&ssid=";
    path += urlEncode(WiFi.SSID().c_str());
    path += "&ip=";
    path += urlEncode(WiFi.localIP().toString().c_str());
    path += "&rssi=";
    path += String(WiFi.RSSI());
    path += "&leds=";
    path += String(LED_NUM);
    path += "&app=";
    path += BACKEND_SERVER;
    return path;
}

/**
 * @brief Connection failed: certificate error, or other error (state set). Stops the connection.
 *
 * @returns CERT or ERROR
 */
static Result connError(Conn& conn, const bool https, const int code)
{
    char msg[64] = "";
    Result res = Result::ERROR;
#if defined(ESP8266)
    // All X.509 errors (not trusted, bad name, expired, ...), as mbedTLS on the ESP32
    const int tlsErr = (https ? conn.secure.getLastSSLError(msg, sizeof(msg)) : 0);
    // Date error with the build time: no real time yet, retry
    if ((tlsErr == BR_ERR_X509_EXPIRED) && tlsTimeGuess) {
        Serial.print("Backend: no time for the certificate check yet\r\n");
    } else if ((tlsErr > BR_ERR_X509_OK) && (tlsErr < (BR_ERR_X509_OK + 32))) {
        Serial.printf("Backend: certificate error %d %s\r\n", tlsErr, msg);
        res = Result::CERT;
    }
#else
    const int tlsErr = (https ? conn.secure.lastError(msg, sizeof(msg)) : 0);
    if ((tlsErr == MBEDTLS_CERT_VERIFY_FAILED) || (tlsErr == MBEDTLS_X509_FATAL)) {
        res = Result::CERT;
    }
#endif
    if (res == Result::ERROR) {
        if (https) {
            Serial.printf("Backend: TLS error %d %s, heap %u free, %u largest block\r\n", tlsErr, msg,
                (unsigned)ESP.getFreeHeap(), (unsigned)heapMaxBlock());
        }
        setState(BackendState::ERROR, HTTPClient::errorToString(code).c_str());
    }
    conn.plain.stop();
    conn.secure.stop();
    return res;
}

/**
 * @brief Open the WebSocket connection
 *
 * @returns OPEN, ERROR (state set) or CERT
 */
static Result openWs(Connection& st, const BackendCfg& cfg)
{
    UrlParts url;
    if (!parseUrl(cfg.url, url)) {
        setState(BackendState::ERROR, "bad URL");
        return Result::ERROR;
    }
    WiFiClient* client = setupClient(st.conn, cfg);
    const int code = wsHandshake(*client, cfg, url, devicePath());
    if (code < 0) {
        return connError(st.conn, url.https, code);
    }
    if (code != 101) {
        client->stop();
        char msg[24];
        snprintf(msg, sizeof(msg), "HTTP %d", code);
        setState(BackendState::ERROR, msg);
        return Result::ERROR;
    }
    st.client = client;
    return Result::OPEN;
}

/**
 * @brief Connect
 *
 * @returns OPEN (stream is set), ERROR or CERT
 */
static Result openStream(const BackendCfg& cfg, const uint32_t settingsVer, const uint32_t reconnectVer)
{
    std::unique_ptr<Connection> st(new (std::nothrow) Connection);
    if (!st) {
        setState(BackendState::ERROR, "no memory");
        return Result::ERROR;
    }
    const Result res = openWs(*st, cfg);
    if (res != Result::OPEN) {
        return res;
    }
    setState(BackendState::CONNECTED);
    helloOk = false;
    badServer[0] = '\0';
    st->settingsVer = settingsVer;
    st->reconnectVer = reconnectVer;
    st->repRev = settingsRev();
    st->repLedRev = ledRemoteRev();
    st->lastData = millis();
    reportDue = false;
    stream = std::move(st);
    return Result::OPEN;
}

/**
 * @brief Close the connection: close frame, then stop
 */
static void closeStream()
{
    if (otaRun.active) {
        otaStop("connection lost");
    }
    if (stream) {
        const uint8_t code[2] = { 0x03, 0xe8 };  // 1000: normal closure
        wsSend(*stream->client, WS_CLOSE, code, sizeof(code));
        stream->client->stop();
        stream.reset();
    }
}

/**
 * @brief Wait before next connect, stop early on change
 */
static void startWait(const uint32_t ms, const uint32_t settingsVer, const uint32_t reconnectVer)
{
    waiting = true;
    waitStart = millis();
    waitMs = ms;
    waitSettingsVer = settingsVer;
    waitReconnectVer = reconnectVer;
}

/**
 * @brief Mark certificate as untrusted
 */
static void certUntrusted(const uint32_t settingsVer)
{
    {
        Lock lock(mtx);
        certVersion = settingsVer;
        certAsked = false;
    }
    setState(BackendState::CERT_UNTRUSTED);
}

/**
 * @brief Allocate message buffer
 *
 * Before any TLS connection (heap in one piece).
 *
 * @returns true if allocated
 */
static bool allocData()
{
    if (msgData == nullptr) {
        msgData = (char*)malloc(BACKEND_DATA_MAX);
        if (msgData == nullptr) {
            setState(BackendState::ERROR, "no memory");
        }
    }
    return (msgData != nullptr);
}

/**
 * @brief Run one backend step
 *
 * Reads the open stream, or waits, or connects.
 */
static void backendStep()
{
    if (!allocData()) {
        return;
    }

    // Open stream
    if (stream) {
        BackendCfg cfg;
        settingsGetBackend(cfg);
        const Result res = pollStream(*stream, cfg);
        if (res == Result::OPEN) {
            return;
        }
        const uint32_t settingsVer = stream->settingsVer;
        const uint32_t reconnectVer = stream->reconnectVer;
        closeStream();
        // Stream was open: start retries again, a wrong server only after the longest wait
        retryMs = ((badServer[0] != '\0') ? BACKEND_RETRY_MAX_MS : BACKEND_RETRY_MIN_MS);
        if (res == Result::ERROR) {
            startWait(retryMs, settingsVer, reconnectVer);
            retryMs = min(retryMs * 2, BACKEND_RETRY_MAX_MS);
        }
        return;
    }

    // Retry delay
    if (waiting) {
        if (((millis() - waitStart) < waitMs) && !isChanged(waitSettingsVer, waitReconnectVer)) {
            return;
        }
        waiting = false;
    }

    BackendCfg cfg;
    const uint32_t settingsVer = settingsGetBackend(cfg);
    const uint32_t reconnectVer = reconnectCnt.load();
    if (cfg.url[0] == '\0') {
        setState(BackendState::NONE);
        return;
    }
    if (WiFi.status() != WL_CONNECTED) {
        setState(BackendState::WAIT_WIFI);
        return;
    }

    // Wait for user decision
    if ((backendGetStatus().state == BackendState::CERT_UNTRUSTED) && (certVersion == settingsVer)) {
        return;
    }

    setState(BackendState::CONNECTING);
    const Result res = openStream(cfg, settingsVer, reconnectVer);
    if (res == Result::CERT) {
        certUntrusted(settingsVer);
    } else if (res == Result::ERROR) {
        startWait(retryMs, settingsVer, reconnectVer);
        retryMs = min(retryMs * 2, BACKEND_RETRY_MAX_MS);
    }
}

#if defined(ESP8266)
void backendInit()
{
    // Time for the certificate check (BearSSL), not for the display
    configTime(0, 0, "pool.ntp.org", "time.google.com");
    caStore.check();
    allocData();
}

void backendUpdate()
{
    backendStep();
}
#else
/**
 * @brief Backend client task
 */
static void backendTask(void*)
{
    while (true) {
        backendStep();
        delay(20);
    }
}

void backendInit()
{
    // Message buffer before any TLS connection
    allocData();
    xTaskCreatePinnedToCore(backendTask, "backend", 8192, nullptr, 1, nullptr, 0);
}

void backendUpdate()
{
}
#endif

BackendStatus backendGetStatus()
{
    Lock lock(mtx);
    BackendStatus st = status;
    st.stale = ((st.state != BackendState::CONNECTED) || ((millis() - lastDataMs) > (heartbeatMs + BACKEND_MARGIN_MS)));
    return st;
}

const char* ledStatusName(const LedStatus status)
{
    static const char* const names[] = { "UNKNOWN", "STOPPED", "RUNNING", "WAITING" };
    return names[(int)status];
}

const char* ledResultName(const LedResult result)
{
    static const char* const names[] = { "UNKNOWN", "PASS", "FAIL", "WARN" };
    return names[(int)result];
}

bool backendGetLed(const int ix, LedInfo& led)
{
    Lock lock(mtx);
    if ((ix < 0) || (ix >= LED_NUM)) {
        memset(&led, 0, sizeof(led));
    } else {
        led = ledInfos[ix];
        if ((millis() - lastDataMs) > LED_UNKNOWN_AFTER_MS) {
            led.status = LedStatus::UNKNOWN;
        }
    }
    return ledsValid;
}

bool backendGetLedStates(LedState* states, const int num)
{
    Lock lock(mtx);
    const bool lost = ((millis() - lastDataMs) > LED_UNKNOWN_AFTER_MS);
    for (int ix = 0; ix < num; ix++) {
        if (ix < LED_NUM) {
            states[ix] = { ledInfos[ix].used, ledInfos[ix].result,
                (lost ? LedStatus::UNKNOWN : ledInfos[ix].status) };
        } else {
            states[ix] = { false, LedResult::UNKNOWN, LedStatus::UNKNOWN };
        }
    }
    return ledsValid;
}

bool backendGetUnixMs(int64_t& unixMs)
{
    Lock lock(mtx);
    if (!timeValid) {
        return false;
    }
    unixMs = timeUnixMs + (millis() - timeMs);
    return true;
}

bool backendGetTime(struct tm& tm)
{
    Lock lock(mtx);
    if (!timeValid) {
        return false;
    }
    const time_t now = (timeLocalMs + (millis() - timeMs)) / 1000;
    gmtime_r(&now, &tm);
    return true;
}

OtaStatus backendGetOta()
{
    Lock lock(mtx);
    return ota;
}

bool backendOtaAsk()
{
    Lock lock(mtx);
    if ((ota.state == OtaState::AVAILABLE) && !otaAsked) {
        otaAsked = true;
        return true;
    }
    return false;
}

void backendOtaStart()
{
    const OtaState state = backendGetOta().state;
    if ((state == OtaState::AVAILABLE) || (state == OtaState::FAILED)) {
        otaStart = true;
    }
}

void backendReconnect()
{
    reconnectCnt++;
}

bool backendCertAsk()
{
    Lock lock(mtx);
    if ((status.state == BackendState::CERT_UNTRUSTED) && !certAsked) {
        certAsked = true;
        return true;
    }
    return false;
}
