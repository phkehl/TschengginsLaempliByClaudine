// Configuration shell on the serial port
//
// Use a serial terminal at 115200 baud, e.g. `fw/.venv/bin/pio device monitor -d fw`. The shell echoes the input. Enter
// (CR, LF or CRLF) runs the line, Backspace deletes, Ctrl-C cancels. Output lines end with CRLF. Commands:
//
//   help                             Show commands
//   status                           Show WiFi, backend and LED state
//   config                           Show all settings
//   name [<name>|-]                  Show or set device name, "-" for none
//   wifi list                        Show configured networks (tried in this order)
//   wifi scan                        Show networks in range
//   wifi add                         Add network (asks for SSID and password)
//   wifi del <n>                     Delete network n
//   wifi move <n> <m>                Move network n to position m
//   wifi reconnect                   Start a new connect round
//   backend url <url>                Set backend URL, "-" for none
//   backend auth                     Set basic auth (asks for user and password)
//   backend insecure on|off          Certificate check off or on
//   backend reconnect                Reconnect to backend
//   ota                              Show firmware version and update state
//   ota install                      Install newer firmware from backend
//   melody                           Show built-in melodies (numbered)
//   melody <n>|<name>                Play melody (number from the list, or name)
//   melody stop                      Stop melody
//
// The firmware adds more commands (shellExtCommand(), e.g. "display" and "led" on the Chlotz), help lines, status and
// config lines.
//
// For "wifi add", the SSID is a number from the last scan or any name (for hidden networks). An existing SSID gets
// the new password. Password inputs show '*'. The input is UTF-8: one backspace removes one character, the limits
// (name, SSID, ...) are in bytes.

#include <Arduino.h>

#include "backend.hpp"
#include "compat.hpp"
#include "config.hpp"
#include "device.hpp"
#include "jobsound.hpp"
#include "melody.hpp"
#include "rtttl.hpp"
#include "settings.hpp"
#include "shell.hpp"
#include "wifi.hpp"

#include <fw_version.h>

enum class Mode
{
    COMMAND,
    WIFI_SSID,
    WIFI_PASS,
    AUTH_USER,
    AUTH_PASS,
};

static Mode mode = Mode::COMMAND;
static char line[160] = "";
static int lineLen = 0;
static bool lastCr = false;
static char newSsid[33] = "";
static char newUser[33] = "";

static constexpr int MAX_SCAN = 32;
static WifiNet scanNets[MAX_SCAN];
static int numScan = 0;
static bool scanning = false;

static BackendState lastBackend = BackendState::NONE;
static OtaState lastOta = OtaState::NONE;

/**
 * @brief Print prompt for current mode
 */
static void printPrompt()
{
    switch (mode) {
        case Mode::COMMAND:
            Serial.print("> ");
            break;
        case Mode::WIFI_SSID:
            Serial.print("SSID (number from scan, or name): ");
            break;
        case Mode::WIFI_PASS:
            Serial.print("Password (empty for open network): ");
            break;
        case Mode::AUTH_USER:
            Serial.print("User (empty for no auth): ");
            break;
        case Mode::AUTH_PASS:
            Serial.print("Password: ");
            break;
    }
}

/**
 * @brief Print command help
 */
static void cmdHelp()
{
    Serial.println("Commands:");
    Serial.println("  help                             Show commands");
    Serial.println("  status                           Show WiFi, backend and LED state");
    Serial.println("  config                           Show all settings");
    Serial.println("  name [<name>|-]                  Show or set device name");
    Serial.println("  wifi list                        Show configured networks");
    Serial.println("  wifi scan                        Show networks in range");
    Serial.println("  wifi add                         Add network or change password");
    Serial.println("  wifi del <n>                     Delete network n");
    Serial.println("  wifi move <n> <m>                Move network n to position m");
    Serial.println("  wifi reconnect                   Start new connect round");
    Serial.println("  backend url <url>                Set backend URL, - for none");
    Serial.println("  backend auth                     Set basic auth user and password");
    Serial.println("  backend insecure on|off          Certificate check off or on");
    Serial.println("  backend reconnect                Reconnect to backend");
    Serial.println("  ota                              Show firmware and update state");
    Serial.println("  ota install                      Install newer firmware");
    Serial.println("  melody                           Show melodies");
    Serial.println("  melody <n>|<name>                Play melody");
    Serial.println("  melody stop                      Stop melody");
    shellExtHelp();
}

/**
 * @brief Print backend state
 */
static void printBackend()
{
    static const char* names[] = { "not set", "waiting for WiFi", "connecting", "connected", "error",
        "certificate not trusted" };
    const BackendStatus st = backendGetStatus();
    Serial.printf("Backend: %s", names[(int)st.state]);
    if (st.state == BackendState::CONNECTED) {
        Serial.printf(", state version %u, %d LEDs used, %u changes, %u updates", (unsigned)st.version, st.leds,
            (unsigned)st.changes, (unsigned)st.updates);
    } else if (st.state == BackendState::ERROR) {
        Serial.printf(", %s", st.error);
    }
    Serial.print("\r\n");
}

/**
 * @brief Print firmware and update state
 */
static void printOta()
{
    static const char* names[] = { "up to date", "available", "installing", "failed", "done" };
    const OtaStatus ota = backendGetOta();
    Serial.printf("Firmware: %s %s, update %s", FW_NAME, FW_VERSION, names[(int)ota.state]);
    if (ota.state != OtaState::NONE) {
        Serial.printf(": %s", ota.version);
    }
    if (ota.state == OtaState::RUNNING) {
        Serial.printf(" (%d %%)", ota.progress);
    } else if (ota.state == OtaState::FAILED) {
        Serial.printf(" (%s)", ota.error);
    }
    Serial.print("\r\n");
}

/**
 * @brief Print melody that plays
 */
static void printMelody()
{
    const int ix = melodyPlaying();
    if (ix < 0) {
        Serial.println("Melody: off");
        return;
    }
    char name[24];
    rtttlName(ix, name, sizeof(name));
    Serial.printf("Melody: %d %s plays\r\n", ix + 1, name);
}

/**
 * @brief Print sounds of an LED, if any
 */
static void printSounds(const LedInfo& led)
{
    static const char* const names[JOB_SOUNDS] = { "pass->fail", "fail", "fail->pass", "pass" };
    char text[100] = "";
    for (int ev = 0; ev < JOB_SOUNDS; ev++) {
        if (led.sounds[ev] == SOUND_NONE) {
            continue;
        }
        char name[24];
        jobSoundName(led.sounds[ev], name, sizeof(name));
        const size_t len = strlen(text);
        snprintf(text + len, sizeof(text) - len, "%s%s %s", ((len > 0) ? ", " : ""), names[ev], name);
    }
    if (text[0] != '\0') {
        Serial.printf("        sounds: %s\r\n", text);
    }
}

/**
 * @brief Print LED states from backend
 */
static void printLeds()
{
    static LedInfo led;
    for (int ix = 0; ix < LED_NUM; ix++) {
        if (!backendGetLed(ix, led)) {
            Serial.println("LED state: none from backend yet");
            return;
        }
        if (!led.used) {
            Serial.printf("LED %2d: off\r\n", ix + 1);
            continue;
        }
        Serial.printf("LED %2d: %s: %s: %s, status %s%s%s, result %s\r\n", ix + 1, led.label, led.source, led.path,
            ledStatusName(led.status), ((led.detail[0] != '\0') ? " " : ""), led.detail,
            ledResultName(led.result));
        for (int line = 0; line < led.numInfo; line++) {
            Serial.printf("        %s: %s\r\n", led.info[line].name, led.info[line].value);
        }
        printSounds(led);
    }
}

/**
 * @brief Print WiFi, backend and LED state
 */
static void cmdStatus()
{
    static const char* names[] = { "no network", "paused", "scanning", "connecting", "connected" };
    const WifiState state = wifiGetState();
    Serial.printf("WiFi: %s", names[(int)state]);
    if ((state == WifiState::CONNECTING) || (state == WifiState::CONNECTED)) {
        Serial.printf(", %s", wifiGetSsid());
    }
    if (state == WifiState::CONNECTED) {
        Serial.printf(", %s, %d dBm", wifiGetIp().toString().c_str(), wifiGetRssi());
    }
    Serial.print("\r\n");
    printBackend();
    printOta();
    Serial.printf("Device ID: %s\r\n", deviceId());
    struct tm tm;
    if (backendGetTime(tm)) {
        char text[24];
        strftime(text, sizeof(text), "%Y-%m-%d %H:%M:%S", &tm);
        Serial.printf("Time: %s%s\r\n", text, (backendGetStatus().stale ? " (no heartbeat)" : ""));
    } else {
        Serial.println("Time: unknown");
    }
    shellExtStatus();
    printMelody();
    Serial.printf("Heap: %u free, %u largest block, %u lowest free\r\n", (unsigned)ESP.getFreeHeap(),
        (unsigned)heapMaxBlock(), (unsigned)heapMinFree());
    printLeds();
}

/**
 * @brief Print configured networks
 */
static void cmdWifiList()
{
    const int num = wifiNumNets();
    if (num == 0) {
        Serial.println("No networks configured");
        return;
    }
    const bool connected = (wifiGetState() == WifiState::CONNECTED);
    for (int ix = 0; ix < num; ix++) {
        const WifiCfg& cfg = wifiGetNet(ix);
        const bool active = (connected && (strcmp(cfg.ssid, wifiGetSsid()) == 0));
        Serial.printf("  %d: %-32s %-8s %s\r\n", ix + 1, cfg.ssid, ((cfg.pass[0] != '\0') ? "password" : "open"),
            (active ? "connected" : ""));
    }
}

/**
 * @brief Show or set device name
 *
 * @param[in] name  New name (may have spaces), "-" for none, nullptr to show
 */
static void cmdName(const char* name)
{
    if (name != nullptr) {
        if (!settingsSetName((strcmp(name, "-") == 0) ? "" : name)) {
            Serial.printf("Usage: name <max. %d bytes, letters, digits, - _ .> | name -\r\n", DEVICE_NAME_MAX);
            return;
        }
    }
    char text[DEVICE_NAME_MAX + 1];
    settingsGetName(text, sizeof(text));
    Serial.printf("Device name: %s\r\n", ((text[0] != '\0') ? text : "(not set)"));
}

/**
 * @brief Print all settings
 */
static void cmdConfig()
{
    Serial.printf("Device ID: %s\r\n", deviceId());
    cmdName(nullptr);
    Serial.println("WiFi networks:");
    cmdWifiList();
    BackendCfg cfg;
    settingsGetBackend(cfg);
    Serial.printf("Backend URL: %s\r\n", ((cfg.url[0] != '\0') ? cfg.url : "(not set)"));
    Serial.printf("Backend user: %s\r\n", ((cfg.user[0] != '\0') ? cfg.user : "(no auth)"));
    Serial.printf("Backend password: %s\r\n", ((cfg.pass[0] != '\0') ? "****" : "(none)"));
    Serial.printf("Backend certificate check: %s\r\n", (cfg.insecure ? "off" : "on"));
    shellExtConfig();
}

/**
 * @brief Print scan results
 */
static void printScan()
{
    if (numScan < 0) {
        Serial.println("Scan failed");
        return;
    }
    if (numScan == 0) {
        Serial.println("No networks found");
        return;
    }
    for (int ix = 0; ix < numScan; ix++) {
        const WifiNet& net = scanNets[ix];
        Serial.printf("  %2d: %-32s %4d dBm %s\r\n", ix + 1, net.ssid, net.rssi, (net.secure ? "" : "open"));
    }
}

long shellParseNumber(const char* str)
{
    if ((str == nullptr) || (str[0] == '\0')) {
        return -1;
    }
    for (const char* ch = str; *ch != '\0'; ch++) {
        if (!isdigit(*ch)) {
            return -1;
        }
    }
    return atol(str);
}

/**
 * @brief Parse 1-based list number
 *
 * @returns the 0-based index, -1 if invalid
 */
static int parseIndex(const char* str, const int num)
{
    const long val = shellParseNumber(str);
    return (((val >= 1) && (val <= num)) ? (val - 1) : -1);
}

/**
 * @brief Add network, print result
 */
static void addNet(const char* pass)
{
    if (wifiAddNet(newSsid, pass)) {
        Serial.printf("Saved network %s\r\n", newSsid);
    } else {
        Serial.printf("Cannot add network (max. %d)\r\n", WIFI_MAX_NETS);
    }
}

/**
 * @brief Handle WiFi SSID input
 */
static void inputWifiSsid()
{
    if (lineLen == 0) {
        Serial.println("Cancelled");
        mode = Mode::COMMAND;
        return;
    }

    // Number from scan or name
    const int scanIx = parseIndex(line, numScan);
    if (scanIx >= 0) {
        strlcpy(newSsid, scanNets[scanIx].ssid, sizeof(newSsid));
        Serial.printf("SSID: %s\r\n", newSsid);
        if (!scanNets[scanIx].secure) {
            addNet("");
            mode = Mode::COMMAND;
            return;
        }
    } else if (lineLen > 32) {
        Serial.println("SSID too long (max. 32 chars)");
        return;
    } else {
        strlcpy(newSsid, line, sizeof(newSsid));
    }
    mode = Mode::WIFI_PASS;
}

/**
 * @brief Handle WiFi password input
 */
static void inputWifiPass()
{
    if (!wifiPassValid(line)) {
        Serial.println("Password must have 8-63 chars");
        return;
    }
    addNet(line);
    mode = Mode::COMMAND;
}

/**
 * @brief Handle auth user input
 */
static void inputAuthUser()
{
    if (lineLen > 32) {
        Serial.println("User too long (max. 32 chars)");
        return;
    }
    if (lineLen == 0) {
        settingsSetUser("");
        settingsSetPass("");
        Serial.println("Basic auth off");
        mode = Mode::COMMAND;
        return;
    }
    strlcpy(newUser, line, sizeof(newUser));
    mode = Mode::AUTH_PASS;
}

/**
 * @brief Handle auth password input
 */
static void inputAuthPass()
{
    if (lineLen > 64) {
        Serial.println("Password too long (max. 64 chars)");
        return;
    }
    settingsSetUser(newUser);
    settingsSetPass(line);
    Serial.printf("Basic auth user %s saved\r\n", newUser);
    mode = Mode::COMMAND;
}

/**
 * @brief Run melody command
 */
static void cmdMelody(const char* sub)
{
    char name[24];
    if (sub == nullptr) {
        const int num = rtttlCount();
        for (int ix = 0; ix < num; ix++) {
            rtttlName(ix, name, sizeof(name));
            Serial.printf("  %2d: %-16s%s", ix + 1, name, ((((ix % 4) == 3) || (ix == (num - 1))) ? "\r\n" : ""));
        }
        return;
    }
    if (strcmp(sub, "stop") == 0) {
        melodyStop();
        Serial.println("Melody stopped");
        return;
    }
    int ix = parseIndex(sub, rtttlCount());
    if (ix < 0) {
        ix = rtttlFind(sub);
    }
    if ((ix >= 0) && melodyPlay(ix)) {
        rtttlName(ix, name, sizeof(name));
        Serial.printf("Melody %d: %s\r\n", ix + 1, name);
    } else {
        Serial.println("Usage: melody [<n>|<name>|stop], see melody");
    }
}

/**
 * @brief Run wifi command
 */
static void cmdWifi(const char* sub, const char* arg1, const char* arg2)
{
    if ((sub == nullptr) || (strcmp(sub, "list") == 0)) {
        cmdWifiList();
    } else if (strcmp(sub, "scan") == 0) {
        Serial.println("Scanning...");
        wifiScanStart();
        scanning = true;
    } else if (strcmp(sub, "add") == 0) {
        if (numScan <= 0) {
            Serial.println("Tip: use wifi scan first to select by number");
        }
        mode = Mode::WIFI_SSID;
    } else if (strcmp(sub, "del") == 0) {
        const int ix = parseIndex(arg1, wifiNumNets());
        if ((ix >= 0) && wifiDelNet(ix)) {
            cmdWifiList();
        } else {
            Serial.println("Usage: wifi del <n>, see wifi list");
        }
    } else if (strcmp(sub, "move") == 0) {
        const int from = parseIndex(arg1, wifiNumNets());
        const int to = parseIndex(arg2, wifiNumNets());
        if ((from >= 0) && (to >= 0) && wifiMoveNet(from, to)) {
            cmdWifiList();
        } else {
            Serial.println("Usage: wifi move <n> <m>, see wifi list");
        }
    } else if (strcmp(sub, "reconnect") == 0) {
        wifiReconnect();
    } else {
        Serial.println("Unknown wifi command, try help");
    }
}

/**
 * @brief Run backend command
 */
static void cmdBackend(const char* sub, const char* arg1)
{
    if (sub == nullptr) {
        printBackend();
    } else if (strcmp(sub, "url") == 0) {
        const char* url = (((arg1 == nullptr) || (strcmp(arg1, "-") == 0)) ? "" : arg1);
        if (settingsSetUrl(url)) {
            Serial.printf("Backend URL: %s\r\n", ((url[0] != '\0') ? url : "(not set)"));
        } else {
            Serial.println("Usage: backend url http(s)://... (max. 127 chars)");
        }
    } else if (strcmp(sub, "auth") == 0) {
        mode = Mode::AUTH_USER;
    } else if ((strcmp(sub, "insecure") == 0) && (arg1 != nullptr) &&
        ((strcmp(arg1, "on") == 0) || (strcmp(arg1, "off") == 0))) {
        settingsSetInsecure(strcmp(arg1, "on") == 0);
        Serial.printf("Certificate check %s\r\n", ((strcmp(arg1, "on") == 0) ? "off" : "on"));
    } else if (strcmp(sub, "reconnect") == 0) {
        backendReconnect();
    } else {
        Serial.println("Unknown backend command, try help");
    }
}

/**
 * @brief Run command line
 */
static void runCommand()
{
    // Name: rest of the line (may have spaces)
    if ((strncmp(line, "name", 4) == 0) && ((line[4] == '\0') || (line[4] == ' '))) {
        const char* name = line + 4;
        while (*name == ' ') {
            name++;
        }
        cmdName((*name != '\0') ? name : nullptr);
        return;
    }

    char* cmd = strtok(line, " ");
    char* sub = strtok(nullptr, " ");
    char* arg1 = strtok(nullptr, " ");
    char* arg2 = strtok(nullptr, " ");
    char* arg3 = strtok(nullptr, " ");
    if (cmd == nullptr) {
        return;
    }

    if (strcmp(cmd, "help") == 0) {
        cmdHelp();
    } else if (strcmp(cmd, "status") == 0) {
        cmdStatus();
    } else if (strcmp(cmd, "config") == 0) {
        cmdConfig();
    } else if (strcmp(cmd, "wifi") == 0) {
        cmdWifi(sub, arg1, arg2);
    } else if (strcmp(cmd, "backend") == 0) {
        cmdBackend(sub, arg1);
    } else if (strcmp(cmd, "ota") == 0) {
        if (sub == nullptr) {
            printOta();
        } else if (strcmp(sub, "install") == 0) {
            const OtaState state = backendGetOta().state;
            if (!otaPossible()) {
                Serial.println("No firmware update here, use USB");
            } else if ((state == OtaState::AVAILABLE) || (state == OtaState::FAILED)) {
                backendOtaStart();
                Serial.println("Installing firmware...");
            } else {
                Serial.println("No firmware update available");
            }
        } else {
            Serial.println("Usage: ota [install]");
        }
    } else if (strcmp(cmd, "melody") == 0) {
        cmdMelody(sub);
    } else if (!shellExtCommand(cmd, sub, arg1, arg2, arg3)) {
        Serial.printf("Unknown command %s, try help\r\n", cmd);
    }
}

/**
 * @brief Handle one complete line
 */
static void handleLine()
{
    line[lineLen] = '\0';
    switch (mode) {
        case Mode::COMMAND:
            runCommand();
            break;
        case Mode::WIFI_SSID:
            inputWifiSsid();
            break;
        case Mode::WIFI_PASS:
            inputWifiPass();
            break;
        case Mode::AUTH_USER:
            inputAuthUser();
            break;
        case Mode::AUTH_PASS:
            inputAuthPass();
            break;
    }
    lineLen = 0;
}

void shellInit()
{
    Serial.print("\r\n");
    Serial.printf("Tschenggins Laempli %s, %s %s. Type help for commands.\r\n", deviceId(), FW_NAME, FW_VERSION);
    printPrompt();
}

void shellUpdate()
{
    while (Serial.available() > 0) {
        const int ch = Serial.read();
        const bool cr = (ch == '\r');
        if ((ch == '\n') && lastCr) {
            // LF after CR: already handled
        } else if (cr || (ch == '\n')) {
            Serial.print("\r\n");
            handleLine();
            if (!scanning) {
                printPrompt();
            }
        } else if ((ch == 0x08) || (ch == 0x7f)) {
            if (lineLen > 0) {
                // Remove all bytes of one character
                do {
                    lineLen--;
                } while ((lineLen > 0) && ((line[lineLen] & 0xc0) == 0x80));
                Serial.print("\b \b");
            }
        } else if (ch == 0x03) {
            Serial.println("^C");
            mode = Mode::COMMAND;
            lineLen = 0;
            printPrompt();
        } else if ((ch >= 0x20) && (lineLen < (int)(sizeof(line) - 1))) {
            line[lineLen] = ch;
            lineLen++;
            const bool secret = ((mode == Mode::WIFI_PASS) || (mode == Mode::AUTH_PASS));
            if (!secret) {
                Serial.print((char)ch);
            } else if ((ch & 0xc0) != 0x80) {
                Serial.print('*');
            }
        }
        lastCr = cr;
    }

    // Print scan when done
    if (scanning) {
        const int res = wifiScanResult(scanNets, MAX_SCAN);
        if (res != -1) {
            scanning = false;
            numScan = res;
            printScan();
            wifiResume();
            printPrompt();
        }
    }

    // Hint for untrusted certificate
    const BackendState state = backendGetStatus().state;
    if ((state == BackendState::CERT_UNTRUSTED) && (lastBackend != BackendState::CERT_UNTRUSTED)) {
        Serial.println("Backend certificate not trusted. Use: backend insecure on");
    }
    lastBackend = state;

    // Hints for firmware update
    const OtaStatus ota = backendGetOta();
    if (ota.state != lastOta) {
        if (ota.state == OtaState::AVAILABLE) {
            Serial.printf("Firmware update available: %s. Use: %s\r\n", ota.version,
                (otaPossible() ? "ota install" : "USB"));
        } else if (ota.state == OtaState::FAILED) {
            Serial.printf("Firmware update failed: %s\r\n", ota.error);
        }
        lastOta = ota.state;
    }
}
