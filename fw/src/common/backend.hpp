// Backend client: WebSocket connection to the backend

#ifndef BACKEND_HPP
#define BACKEND_HPP

#include <stdint.h>
#include <time.h>

#include "config.hpp"

// Backend connection state
enum class BackendState
{
    NONE,            // No URL configured
    WAIT_WIFI,       // Waiting for WiFi
    CONNECTING,      // Connecting
    CONNECTED,       // Stream open
    ERROR,           // Connection failed, retry later
    CERT_UNTRUSTED,  // Certificate check failed, waiting for user
};

// Backend status for UI and shell
struct BackendStatus
{
    BackendState state;
    char error[48];     // Last error, if state is ERROR
    int leds;           // Number of configured LEDs in last state
    uint32_t version;   // Version of last state
    bool stale;         // No data within heartbeat interval (or not connected)
    uint32_t changes;   // Number of LED status or result changes
    uint32_t updates;   // Number of other LED state changes
    uint32_t settingsApplied;  // Number of applied settings events (web page)
};

// Result of the last finished run of a job (LED colour)
enum class LedResult
{
    UNKNOWN,  // No result yet, canceled, or unknown job
    PASS,
    FAIL,
    WARN,
};

// What a job does now (LED light)
enum class LedStatus
{
    UNKNOWN,  // No data for the job, or no backend data for LED_UNKNOWN_AFTER_MS
    STOPPED,  // Steady
    RUNNING,  // Slow pulse
    WAITING,  // Fast pulse
};

// Job sound events, most important first (see jobsound.cpp)
enum class JobSound
{
    BROKEN,  // Stopped with FAIL or WARN, was PASS
    FAIL,    // Stopped with FAIL or WARN
    FIXED,   // Stopped with PASS, was FAIL or WARN
    PASS,    // Stopped with PASS
};
static constexpr int JOB_SOUNDS = 4;

// One info line of a job
struct LedInfoLine
{
    char name[12];
    char value[48];
};

// LED state from the backend
struct LedInfo
{
    bool used;             // LED has a job
    LedResult result;
    LedStatus status;
    char label[41];        // Short name for the display
    char source[16];       // Source of the job, e.g. "gitlab"
    char path[LED_PATH_SIZE];  // Rest of the job path, " / " between names
    char detail[24];       // Status text from the source, "" = none
    double time;           // Last change [unix s], 0 = unknown
    int numInfo;
    LedInfoLine info[(LED_INFO_MAX > 0) ? LED_INFO_MAX : 1];  // Info lines (none if LED_INFO_MAX is 0)
    int8_t sounds[JOB_SOUNDS];  // Sound ID per event (JobSound, see jobsound.hpp)
};

// Short LED state (LED task, screensaver)
struct LedState
{
    bool used;
    LedResult result;
    LedStatus status;
};

// Firmware update (OTA) state
enum class OtaState
{
    NONE,       // No newer firmware
    AVAILABLE,  // Newer firmware on backend
    RUNNING,    // Download and flash
    FAILED,     // Update failed
    DONE,       // Done, restarting
};

// Firmware update status
struct OtaStatus
{
    OtaState state;
    char version[48];   // Version on backend
    int progress;       // Progress [%] while RUNNING
    char error[48];     // Error text if FAILED
};

/**
 * @brief Start backend client
 *
 * ESP32: starts the backend task. ESP8266: call backendUpdate() in the loop.
 */
void backendInit();

/**
 * @brief Run backend client (ESP8266)
 *
 * Call in the loop. Does nothing on the ESP32 (own task).
 */
void backendUpdate();

/**
 * @brief Get backend status
 *
 * Thread-safe.
 *
 * @returns a copy of the status
 */
BackendStatus backendGetStatus();

/**
 * @brief Get state of one LED (with path and info lines)
 *
 * Thread-safe. LEDs beyond the backend state are unused. Without backend data for LED_UNKNOWN_AFTER_MS, the status of
 * a used LED is UNKNOWN.
 *
 * @param[in]  ix   LED index (0 = LED 1)
 * @param[out] led  LED state
 *
 * @returns true if a state came from the backend since start
 */
bool backendGetLed(const int ix, LedInfo& led);

/**
 * @brief Get used, status and result of the LEDs
 *
 * Thread-safe, small copy (LED task, each frame). Unused and unknown as backendGetLed().
 *
 * @param[out] states  LED states
 * @param[in]  num     Number of LEDs
 *
 * @returns true if a state came from the backend since start
 */
bool backendGetLedStates(LedState* states, const int num);

/**
 * @brief Get name of a job status
 *
 * @param[in] status  Status
 *
 * @returns the name as the backend sends it, e.g. "RUNNING"
 */
const char* ledStatusName(const LedStatus status);

/**
 * @brief Get name of a job result
 *
 * @param[in] result  Result
 *
 * @returns the name as the backend sends it, e.g. "PASS"
 */
const char* ledResultName(const LedResult result);

/**
 * @brief Get UTC time from backend
 *
 * @param[out] unixMs  Unix time [ms]
 *
 * @returns true if the time was received at least once
 */
bool backendGetUnixMs(int64_t& unixMs);

/**
 * @brief Get local time from backend
 *
 * The backend sends its time and UTC offset with each heartbeat. In between, the time runs on millis().
 *
 * @param[out] tm  Local time
 *
 * @returns true if the time was received at least once
 */
bool backendGetTime(struct tm& tm);

/**
 * @brief Get firmware update status
 *
 * Thread-safe.
 *
 * @returns a copy of the status
 */
OtaStatus backendGetOta();

/**
 * @brief Check for update question
 *
 * @returns true once for each new firmware version
 */
bool backendOtaAsk();

/**
 * @brief Start firmware update
 *
 * Only if state is AVAILABLE or FAILED. The device restarts after a successful update.
 */
void backendOtaStart();

/**
 * @brief Reconnect now
 */
void backendReconnect();

/**
 * @brief Check for certificate question
 *
 * @returns true once for each untrusted certificate (until the settings change)
 */
bool backendCertAsk();

#endif // BACKEND_HPP
