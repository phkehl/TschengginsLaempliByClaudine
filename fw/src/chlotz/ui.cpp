// User interface: screens and touch
//
// Main screen (320x240 landscape):
//
//   y   0-17   title                                      SSID + signal bars
//   y  22-213  content: one row per LED: colour, LED number, status, result, label
//   y 218-239  backend status                                        [menu]
//
// The SSID blinks while connecting. Connected shows 1-4 bars. A tap on an LED row shows the LED details (LED_INFO).
//
// Settings screens (title, 6 rows, buttons at the bottom):
//
// - MENU:     Device, Display, Test, [Back] [About]
// - DEV_CFG:  Name (letters, digits, space, - _ .), WiFi networks, Backend, Firmware/Update, [Back]
// - TEST:     LED test, Rainbow demo, Melodies, [Back]
// - NETS:     configured networks (tried in this order), [Back] [Add] [Up] [Down] [Del]. Tap a row to select it.
// - SCAN:     "Type SSID..." and the networks in range, [Back] [Up] [Down] [Scan]. Tap a row to add it.
// - BACKEND:  URL, user, password, certificate check (tap to toggle), status, [Back] [Reconnect]
// - DISPLAY:  dim timeout, dim level, screensaver timeout, LED brightness, LED animation of RUNNING and of
//             WAITING jobs (tap: PICK)
// - PICK:     options of LED brightness or of an animation, [Back] ([Up] [Down] if more than 6). A tap selects it (at
//             once, the LEDs show it); the selected one has a yellow frame. [Back] goes to DISPLAY.
// - KEYBOARD: text input (see keyboard.cpp)
// - CERT:     question if the backend may be used without certificate check, [No] [Yes]. Shows once on the main
//             screen when the backend certificate is not trusted.
// - LOGO:     logo (logo.png, embedded), full screen, device ID top left, device name top right,
//             firmware version bottom left. After start for LOGO_MS (taps do nothing), then the main screen. From the
//             menu ("About"): a tap goes back to the menu.
// - OTA_ASK:  question to install a newer firmware from the backend, [Later] [Install]. Shows once per version on
//             the main screen, and from the device settings ("Update: ...").
// - OTA_RUN:  firmware update progress; on error [OK] goes back to the main screen
// - LED_TEST: LED test sequence (ledtest.cpp): step, name, current, LED colours, [Back] [Prev] [Play/Pause] [Next].
//             The test runs only while this screen shows.
// - DEMO:     rainbow demo on the LEDs: current, LED colours, [Back]. Runs only while this screen shows.
// - MELODY:   built-in melodies (melody.cpp), [Back] [Up] [Down] [Stop]. A tap plays it; the one that plays has a
//             yellow frame. Leaving the screen stops the melody.
// - LED_INFO: details of one LED: source, path, status, result, last change, info lines of the source,
//             [Back] [Prev] [Next].
// - SAVER:    screensaver: the logo with device ID top left, device name top right, date and time (hh:mm:ss) bottom
//             centre. Comes on the main screen (only there) after the screensaver timeout (Display settings, 0 = never)
//             without touch and without a change of an LED status or result. A touch (only ends it) or a status or
//             result change goes back to the main screen, and the timeout starts again. The certificate and update
//             questions also end it.
//
// Settings from the web page (backend.cpp): the screens that show them draw again (settingsRev() changes).
//
// Settings timeout: after UI_MENU_TIMEOUT_MS without touch, all screens except MAIN (and the logo after start) go
// back to the main screen. Open input is discarded, a WiFi scan pause ends, the certificate question counts as [No].
//
// Touch: each screen has targets (buttons, list rows, keys) with an ID. Touch down draws the target pressed.
// Release draws it normal and runs its action, if the finger is still on the same target. A touch on the dimmed
// display only restores the brightness.

#include <Arduino.h>

#include "backend.hpp"
#include "config.hpp"
#include "chlotzsettings.hpp"
#include "device.hpp"
#include "display.hpp"
#include "keyboard.hpp"
#include "ledstatus.hpp"
#include "ledtest.hpp"
#include "melody.hpp"
#include "rtttl.hpp"
#include "settings.hpp"
#include "ui.hpp"
#include "wifi.hpp"

#include <fw_version.h>

enum class Screen
{
    MAIN,
    MENU,
    DEV_CFG,
    TEST,
    NETS,
    SCAN,
    BACKEND,
    DISP_CFG,
    KEYBOARD,
    CERT,
    LOGO,
    OTA_ASK,
    OTA_RUN,
    LED_TEST,
    DEMO,
    LED_INFO,
    PICK,
    SAVER,
    MELODY,
};

// What the PICK screen selects
enum class PickKind
{
    BRIGHTNESS,
    RUNNING,
    WAITING,
};

// What the keyboard edits
enum class KbMode
{
    WIFI_SSID,
    WIFI_PASS,
    URL,
    USER,
    PASS,
    DIM_TIMEOUT,
    DIM_LEVEL,
    SAVER_TIMEOUT,
    NAME,
};

static Screen screen = Screen::LOGO;
static bool needRedraw = true;

// Logo, embedded by platformio.ini
extern const uint8_t logoStart[] asm("_binary_src_chlotz_logo_png_start");
extern const uint8_t logoEnd[] asm("_binary_src_chlotz_logo_png_end");
static constexpr uint32_t LOGO_MS = 5000;  // Logo time after start
static bool splashDone = false;            // Logo after start done
static uint32_t logoTime = 0;              // Logo drawn

// Main screen layout
static constexpr int HEAD_H = 18;            // Header height
static constexpr int FOOT_Y = 216;           // Footer line
static constexpr int LEDS_Y = 22;            // First LED line
static constexpr int SSID_W = 16;            // SSID chars in header
static constexpr int DISC_R = 6;             // Colour disc radius
static constexpr int MENU_X = 296;           // Menu button
static constexpr int MENU_Y = 217;
static constexpr int MENU_S = 23;
static constexpr int ID_MENU = 0;
static constexpr int ID_LED = 20;            // LED rows: ID_LED + LED index
static constexpr int NUM_W = 2;              // LED row: LED number chars
static constexpr int STATUS_W = 7;           // LED row: status chars
static constexpr int RESULT_W = 7;           // LED row: result chars
static constexpr int LABEL_W = 19;           // LED row: label chars

// Settings screens layout
static constexpr int LIST_Y = 24;
static constexpr int ROW_H = 28;
static constexpr int ROWS = 6;
static constexpr int BTN_Y = 200;
static constexpr int BTN_H = 40;
static constexpr int ID_BTN = 10;  // First button ID, rows are 0-5

// NETS screen state
static int selNet = -1;

// SCAN screen state
static constexpr int MAX_SCAN = 32;
static WifiNet scanNets[MAX_SCAN];
static int numScan = -1;  // -1: scan runs, -2: scan failed
static int scanTop = 0;   // First shown item (0 = "Type SSID...")

// LED_INFO screen state
static int infoLed = 0;

// MELODY screen state
static int melodyTop = 0;      // First shown melody
static int melodyShown = -1;   // Melody shown as playing

// KEYBOARD screen state
static KbMode kbMode = KbMode::WIFI_SSID;
static char newSsid[33] = "";

// Touch state
static constexpr int RELEASE_COUNT = 2;  // Debounce release
static bool touchDown = false;
static bool touchWake = false;           // Touch only woke the display
static int releaseCnt = 0;
static int pressId = -1;
static int lastX = 0;
static int lastY = 0;
static uint32_t lastInput = 0;           // Last touch or screen change
static uint32_t lastChange = 0;          // Last LED status or result change
static int saverSecond = -1;             // Second shown by the screensaver
static LGFX_Sprite saverBg;               // Logo below the screensaver time

// =====================================================================================================================

/**
 * @brief Get backend status text
 *
 * @param[out] buf   Text buffer
 * @param[in]  size  Size of buf
 */
static void backendText(char* buf, const size_t size)
{
    const BackendStatus st = backendGetStatus();
    switch (st.state) {
        case BackendState::NONE:
            snprintf(buf, size, "Backend: not set");
            break;
        case BackendState::WAIT_WIFI:
            snprintf(buf, size, "Backend: waiting for WiFi");
            break;
        case BackendState::CONNECTING:
            snprintf(buf, size, "Backend: connecting...");
            break;
        case BackendState::CONNECTED:
            snprintf(buf, size, "Backend: connected, %d LEDs used", st.leds);
            break;
        case BackendState::ERROR:
            snprintf(buf, size, "Backend: %s", st.error);
            break;
        case BackendState::CERT_UNTRUSTED:
            snprintf(buf, size, "Backend: certificate not trusted");
            break;
    }
}

/**
 * @brief Draw footer: status and time
 *
 * Status colour: green = OK, yellow = warning, red = error. The time blinks if the backend sends no heartbeat.
 */
static void mainFooter()
{
    auto& lcd = displayLcd();
    const BackendStatus st = backendGetStatus();
    const char* text = "";
    uint16_t colour = TFT_YELLOW;
    switch (st.state) {
        case BackendState::NONE:
            text = "not configured";
            break;
        case BackendState::WAIT_WIFI:
            text = "no network";
            colour = TFT_RED;
            break;
        case BackendState::CONNECTING:
            text = "connecting";
            break;
        case BackendState::CONNECTED:
            text = (st.stale ? "no heartbeat" : "connected");
            colour = (st.stale ? TFT_YELLOW : TFT_GREEN);
            break;
        case BackendState::ERROR:
            text = st.error;
            colour = TFT_RED;
            break;
        case BackendState::CERT_UNTRUSTED:
            text = "cert not trusted";
            colour = TFT_RED;
            break;
    }
    lcd.setTextColor(colour, TFT_BLACK);
    lcd.setCursor(0, FOOT_Y + 4);
    lcd.printf("%-17.17s", text);

    // Time, blinks if stale
    char timeText[24] = "0000-00-00 00:00:00";
    struct tm tm;
    if (backendGetTime(tm)) {
        strftime(timeText, sizeof(timeText), "%Y-%m-%d %H:%M:%S", &tm);
    }
    const bool blinkOff = (st.stale && (((millis() / 500) % 2) == 1));
    lcd.setTextColor((st.stale ? TFT_DARKGREY : TFT_WHITE), TFT_BLACK);
    lcd.setCursor(MENU_X - 4 - (19 * FONT_W), FOOT_Y + 4);
    lcd.printf("%-19s", (blinkOff ? "" : timeText));
}

/**
 * @brief Draw menu button with icon
 */
static void drawMenuButton(const bool pressed)
{
    auto& lcd = displayLcd();
    displayButton(MENU_X, MENU_Y, MENU_S, MENU_S, "", pressed);
    const uint16_t fg = (pressed ? TFT_BLACK : TFT_WHITE);
    for (int ix = 0; ix < 3; ix++) {
        lcd.fillRect(MENU_X + 6, MENU_Y + 6 + (ix * 5), MENU_S - 12, 2, fg);
    }
}

/**
 * @brief Draw full main screen
 */
static void mainDraw()
{
    auto& lcd = displayLcd();
    lcd.fillScreen(TFT_BLACK);
    lcd.setTextColor(TFT_YELLOW, TFT_BLACK);
    lcd.setCursor(0, 1);
    lcd.print("Tschenggins Lämpli");
    lcd.drawFastHLine(0, HEAD_H, lcd.width(), TFT_DARKGREY);
    lcd.drawFastHLine(0, FOOT_Y, lcd.width(), TFT_DARKGREY);
    drawMenuButton(false);
}

/**
 * @brief Draw WiFi state in header
 */
static void mainWifi()
{
    auto& lcd = displayLcd();
    const WifiState state = wifiGetState();
    const char* ssid = wifiGetSsid();

    // SSID, blinks while connecting
    const bool busy = ((state == WifiState::CONNECTING) || (state == WifiState::SCANNING));
    const bool blinkOff = (busy && (((millis() / 500) % 2) == 1));
    const char* name = ((ssid[0] != '\0') ? ssid : ((state == WifiState::NONE) ? "no WiFi" : "WiFi"));
    lcd.setTextColor(((state == WifiState::CONNECTED) ? TFT_WHITE : TFT_DARKGREY), TFT_BLACK);
    // Right-aligned, before the signal bars
    lcd.setCursor(294 - (SSID_W * FONT_W), 1);
    lcd.printf("%*.*s", SSID_W, SSID_W, (blinkOff ? "" : name));

    // Signal bars, at least one when connected
    int bars = 0;
    if (state == WifiState::CONNECTED) {
        const int rssi = wifiGetRssi();
        bars = ((rssi >= -55) ? 4 : ((rssi >= -65) ? 3 : ((rssi >= -75) ? 2 : 1)));
    }
    for (int ix = 0; ix < 4; ix++) {
        const int h = 4 + (ix * 3);
        const int x = 298 + (ix * 6);
        lcd.fillRect(x, 0, 4, 16 - h, TFT_BLACK);
        lcd.fillRect(x, 16 - h, 4, h, ((ix < bars) ? TFT_WHITE : lcd.color565(64, 64, 64)));
    }
}

/**
 * @brief Get display colour of a result
 */
static uint16_t resultColour(const LedResult result)
{
    const CRGB colour = ledStatusColour(result);
    return displayLcd().color565(colour.r, colour.g, colour.b);
}

/**
 * @brief Draw one LED row of the main screen
 *
 * @param[in] ix       LED index
 * @param[in] led      LED colour
 * @param[in] info     LED state, nullptr if no state yet
 * @param[in] pressed  Draw pressed state
 */
static void mainLedRow(const int ix, const CRGB& led, const LedInfo* info, const bool pressed)
{
    auto& lcd = displayLcd();
    const int y = LEDS_Y + (ix * FONT_H);
    const uint16_t bg = (pressed ? TFT_WHITE : TFT_BLACK);
    lcd.fillCircle(DISC_R + 2, y + (FONT_H / 2), DISC_R, lcd.color565(led.r, led.g, led.b));
    lcd.drawCircle(DISC_R + 2, y + (FONT_H / 2), DISC_R, TFT_DARKGREY);
    // Columns: number (1-based), status, result, label
    lcd.setCursor(2 * FONT_W, y);
    lcd.setTextColor((pressed ? TFT_BLACK : TFT_DARKGREY), bg);
    lcd.printf("%*d ", NUM_W, ix + 1);
    if ((info == nullptr) || !info->used) {
        lcd.printf("%-*s %-*s %-*s", STATUS_W, ((info == nullptr) ? "-" : "OFF"), RESULT_W, "", LABEL_W, "");
    } else {
        lcd.setTextColor((pressed ? TFT_BLACK : TFT_WHITE), bg);
        lcd.printf("%-*s ", STATUS_W, ledStatusName(info->status));
        lcd.setTextColor((pressed ? TFT_BLACK : resultColour(info->result)), bg);
        lcd.printf("%-*s ", RESULT_W, ledResultName(info->result));
        lcd.setTextColor((pressed ? TFT_BLACK : TFT_WHITE), bg);
        lcd.printf("%-*.*s", LABEL_W, LABEL_W, info->label);
    }
}

/**
 * @brief Update main screen values
 */
static void mainValues(const CRGB* leds, const int num)
{
    mainWifi();

    // Content: one row per LED
    static LedInfo info;
    for (int ix = 0; ix < min(num, LED_NUM); ix++) {
        const bool valid = backendGetLed(ix, info);
        mainLedRow(ix, leds[ix], (valid ? &info : nullptr), (touchDown && (pressId == (ID_LED + ix))));
    }

    mainFooter();
}

// =====================================================================================================================

/**
 * @brief Get button width
 */
static int btnWidth(const int numBtns)
{
    return (320 / max(numBtns, 4));
}

/**
 * @brief Draw a list row
 *
 * @param[in] row       Row on screen (0-5)
 * @param[in] text      Row text, nullptr for empty row
 * @param[in] pressed   Draw pressed state
 * @param[in] selected  Draw selected state
 */
static void drawRow(const int row, const char* text, const bool pressed, const bool selected)
{
    auto& lcd = displayLcd();
    const int y = LIST_Y + (row * ROW_H);
    lcd.fillRect(0, y, 320, ROW_H, TFT_BLACK);
    if (text == nullptr) {
        return;
    }
    const uint16_t bg = (pressed ? TFT_WHITE : lcd.color565(32, 32, 32));
    const uint16_t fg = (pressed ? TFT_BLACK : (selected ? TFT_YELLOW : TFT_WHITE));
    lcd.fillRoundRect(0, y + 1, 320, ROW_H - 2, 4, bg);
    if (selected) {
        lcd.drawRoundRect(0, y + 1, 320, ROW_H - 2, 4, TFT_YELLOW);
    }
    lcd.setTextColor(fg, bg);
    lcd.setCursor(4, y + ((ROW_H - FONT_H) / 2));
    lcd.printf("%-38.38s", text);
}

/**
 * @brief Draw a message in a row
 */
static void drawRowMsg(const int row, const char* msg)
{
    auto& lcd = displayLcd();
    const int y = LIST_Y + (row * ROW_H);
    lcd.fillRect(0, y, 320, ROW_H, TFT_BLACK);
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    lcd.setCursor(4, y + ((ROW_H - FONT_H) / 2));
    lcd.printf("%-38.38s", msg);
}

/**
 * @brief Draw a settings screen frame
 *
 * @param[in] title   Screen title
 * @param[in] labels  Button labels
 * @param[in] num     Number of buttons
 */
static void drawFrame(const char* title, const char* const* labels, const int num)
{
    auto& lcd = displayLcd();
    lcd.fillScreen(TFT_BLACK);
    lcd.setTextColor(TFT_YELLOW, TFT_BLACK);
    lcd.setCursor(0, 0);
    lcd.print(title);
    const int w = btnWidth(num);
    for (int ix = 0; ix < num; ix++) {
        displayButton(ix * w, BTN_Y, w, BTN_H, labels[ix], false);
    }
}

/**
 * @brief Draw one frame button
 */
static void drawFrameButton(const char* const* labels, const int num, const int id, const bool pressed)
{
    const int w = btnWidth(num);
    displayButton((id - ID_BTN) * w, BTN_Y, w, BTN_H, labels[id - ID_BTN], pressed);
}

/**
 * @brief Find settings screen target
 *
 * @returns row (0-5), button ID, or -1
 */
static int hitList(const int x, const int y, const int numBtns, const int numRows)
{
    if (y >= BTN_Y) {
        const int ix = x / btnWidth(numBtns);
        return ((ix < numBtns) ? (ID_BTN + ix) : -1);
    }
    if ((y >= LIST_Y) && (y < (LIST_Y + (ROWS * ROW_H)))) {
        const int row = (y - LIST_Y) / ROW_H;
        if (row < numRows) {
            return row;
        }
    }
    return -1;
}

/**
 * @brief Show a screen
 */
static void showScreen(const Screen newScreen)
{
    if (newScreen != Screen::LED_TEST) {
        ledTestStop();
    }
    if (newScreen != Screen::DEMO) {
        ledDemoStop();
    }
    if ((screen == Screen::MELODY) && (newScreen != Screen::MELODY)) {
        melodyStop();
    }
    screen = newScreen;
    needRedraw = true;
    lastInput = millis();
}

/**
 * @brief Start keyboard input
 */
static void startInput(const KbMode mode, const char* title, const char* initial, const int maxLen)
{
    kbMode = mode;
    kbStart(title, initial, maxLen);
    showScreen(Screen::KEYBOARD);
}

// =====================================================================================================================

static const char* const MENU_BTNS[] = { "Back", "About" };
static constexpr int MENU_NUM_BTNS = 2;
static constexpr int MENU_ROWS = 3;

/**
 * @brief Draw menu rows
 */
static void menuDrawRow(const int row, const bool pressed)
{
    static const char* const items[MENU_ROWS] = { "Device", "Display", "Test" };
    drawRow(row, items[row], pressed, false);
}

/**
 * @brief Run menu action
 */
static void menuAction(const int id)
{
    switch (id) {
        case ID_BTN:
            showScreen(Screen::MAIN);
            break;
        case ID_BTN + 1:
            showScreen(Screen::LOGO);
            break;
        case 0:
            showScreen(Screen::DEV_CFG);
            break;
        case 1:
            showScreen(Screen::DISP_CFG);
            break;
        case 2:
            showScreen(Screen::TEST);
            break;
    }
}

// =====================================================================================================================

static const char* const DEVICE_BTNS[] = { "Back" };
static constexpr int DEVICE_NUM_BTNS = 1;
static constexpr int DEVICE_ROWS = 5;

/**
 * @brief Draw device settings row
 */
static void deviceDrawRow(const int row, const bool pressed)
{
    static const char* const items[DEVICE_ROWS] = { "", "WiFi networks", "Backend", "", "" };
    if (row == 0) {
        char name[DEVICE_NAME_MAX + 1];
        settingsGetName(name, sizeof(name));
        char text[48];
        snprintf(text, sizeof(text), "Name: %s", ((name[0] != '\0') ? name : "(not set)"));
        drawRow(row, text, pressed, false);
        return;
    }
    if (row == 3) {
        const OtaStatus ota = backendGetOta();
        char text[48];
        if ((ota.state == OtaState::AVAILABLE) || (ota.state == OtaState::FAILED)) {
            snprintf(text, sizeof(text), "Update: %s", ota.version);
        } else {
            snprintf(text, sizeof(text), "Firmware: up to date");
        }
        drawRow(row, text, pressed, false);
        return;
    }
    if (row == 4) {
        char text[48];
        snprintf(text, sizeof(text), "Reverse LEDs, flip display: %s", (settingsLedReverse() ? "on" : "off"));
        drawRow(row, text, pressed, false);
        return;
    }
    drawRow(row, items[row], pressed, false);
}

/**
 * @brief Run device settings action
 */
static void deviceAction(const int id)
{
    switch (id) {
        case ID_BTN:
            showScreen(Screen::MENU);
            break;
        case 0: {
            char name[DEVICE_NAME_MAX + 1];
            settingsGetName(name, sizeof(name));
            startInput(KbMode::NAME, "Device name (empty: none)", name, DEVICE_NAME_MAX);
            break;
        }
        case 1:
            showScreen(Screen::NETS);
            break;
        case 2:
            showScreen(Screen::BACKEND);
            break;
        case 3: {
            const OtaState state = backendGetOta().state;
            if ((state == OtaState::AVAILABLE) || (state == OtaState::FAILED)) {
                showScreen(Screen::OTA_ASK);
            }
            break;
        }
        case 4:
            settingsSetLedReverse(settingsLedReverse() ? 0 : 1);
            break;
    }
}

// =====================================================================================================================

static const char* const TEST_BTNS[] = { "Back" };
static constexpr int TEST_NUM_BTNS = 1;
static constexpr int TEST_ROWS = 3;

/**
 * @brief Draw test menu row
 */
static void testDrawRow(const int row, const bool pressed)
{
    static const char* const items[TEST_ROWS] = { "LED test", "Rainbow demo", "Melodies" };
    drawRow(row, items[row], pressed, false);
}

/**
 * @brief Run test menu action
 */
static void testAction(const int id)
{
    switch (id) {
        case ID_BTN:
            showScreen(Screen::MENU);
            break;
        case 0:
            ledTestStart();
            showScreen(Screen::LED_TEST);
            break;
        case 1:
            ledDemoStart();
            showScreen(Screen::DEMO);
            break;
        case 2:
            showScreen(Screen::MELODY);
            break;
    }
}

// =====================================================================================================================

static const char* const MELODY_BTNS[] = { "Back", "Up", "Down", "Stop" };
static constexpr int MELODY_NUM_BTNS = 4;
static constexpr int ID_MELODY_BACK = ID_BTN + 0;
static constexpr int ID_MELODY_UP = ID_BTN + 1;
static constexpr int ID_MELODY_DOWN = ID_BTN + 2;
static constexpr int ID_MELODY_STOP = ID_BTN + 3;

/**
 * @brief Draw one melody row
 */
static void melodyDrawRow(const int row, const bool pressed)
{
    const int item = melodyTop + row;
    if (item >= rtttlCount()) {
        drawRow(row, nullptr, false, false);
        return;
    }
    char name[24];
    rtttlName(item, name, sizeof(name));
    char text[32];
    snprintf(text, sizeof(text), "%2d %s", item + 1, name);
    drawRow(row, text, pressed, (item == melodyShown));
}

/**
 * @brief Draw all melody rows
 */
static void melodyDrawRows()
{
    melodyShown = melodyPlaying();
    for (int row = 0; row < ROWS; row++) {
        melodyDrawRow(row, false);
    }
}

/**
 * @brief Run melody screen action
 */
static void melodyAction(const int id)
{
    switch (id) {
        case ID_MELODY_BACK:
            showScreen(Screen::TEST);
            break;
        case ID_MELODY_UP:
            if (melodyTop > 0) {
                melodyTop = max(0, melodyTop - ROWS);
                melodyDrawRows();
            }
            break;
        case ID_MELODY_DOWN:
            if ((melodyTop + ROWS) < rtttlCount()) {
                melodyTop += ROWS;
                melodyDrawRows();
            }
            break;
        case ID_MELODY_STOP:
            melodyStop();
            melodyDrawRows();
            break;
        default:
            melodyPlay(melodyTop + id);
            melodyDrawRows();
            break;
    }
}

// =====================================================================================================================

static const char* const NETS_BTNS[] = { "Back", "Add", "Up", "Down", "Del" };
static constexpr int NETS_NUM_BTNS = 5;
static constexpr int ID_NETS_BACK = ID_BTN + 0;
static constexpr int ID_NETS_ADD = ID_BTN + 1;
static constexpr int ID_NETS_UP = ID_BTN + 2;
static constexpr int ID_NETS_DOWN = ID_BTN + 3;
static constexpr int ID_NETS_DEL = ID_BTN + 4;

/**
 * @brief Draw one configured network row
 */
static void netsDrawRow(const int row, const bool pressed)
{
    const int num = wifiNumNets();
    if ((num == 0) && (row == 0)) {
        drawRowMsg(row, "No networks. Tap Add.");
        return;
    }
    if (row >= num) {
        drawRow(row, nullptr, false, false);
        return;
    }
    const WifiCfg& cfg = wifiGetNet(row);
    const bool active = ((wifiGetState() == WifiState::CONNECTED) && (strcmp(cfg.ssid, wifiGetSsid()) == 0));
    char text[48];
    snprintf(text, sizeof(text), "%d %-26.26s %-4s %s", row + 1, cfg.ssid, ((cfg.pass[0] != '\0') ? "" : "open"),
        (active ? "on" : ""));
    drawRow(row, text, pressed, (row == selNet));
}

/**
 * @brief Draw all configured network rows
 */
static void netsDrawRows()
{
    for (int row = 0; row < ROWS; row++) {
        netsDrawRow(row, false);
    }
}

/**
 * @brief Run network screen action
 */
static void netsAction(const int id)
{
    const int num = wifiNumNets();
    switch (id) {
        case ID_NETS_BACK:
            wifiResume();
            selNet = -1;
            showScreen(Screen::DEV_CFG);
            break;
        case ID_NETS_ADD:
            if (num < WIFI_MAX_NETS) {
                numScan = -1;
                scanTop = 0;
                wifiScanStart();
                showScreen(Screen::SCAN);
            }
            break;
        case ID_NETS_UP:
            if (selNet > 0) {
                wifiMoveNet(selNet, selNet - 1);
                selNet--;
                netsDrawRows();
            }
            break;
        case ID_NETS_DOWN:
            if ((selNet >= 0) && (selNet < (num - 1))) {
                wifiMoveNet(selNet, selNet + 1);
                selNet++;
                netsDrawRows();
            }
            break;
        case ID_NETS_DEL:
            if (selNet >= 0) {
                wifiDelNet(selNet);
                selNet = -1;
                netsDrawRows();
            }
            break;
        default:
            // Row: toggle selection
            selNet = ((selNet == id) ? -1 : id);
            netsDrawRows();
            break;
    }
}

// =====================================================================================================================

static const char* const SCAN_BTNS[] = { "Back", "Up", "Down", "Scan" };
static constexpr int SCAN_NUM_BTNS = 4;
static constexpr int ID_SCAN_BACK = ID_BTN + 0;
static constexpr int ID_SCAN_UP = ID_BTN + 1;
static constexpr int ID_SCAN_DOWN = ID_BTN + 2;
static constexpr int ID_SCAN_SCAN = ID_BTN + 3;

/**
 * @brief Get number of scan screen items
 */
static int scanNumItems()
{
    return (1 + ((numScan > 0) ? numScan : 0));
}

/**
 * @brief Draw one scan screen row
 */
static void scanDrawRow(const int row, const bool pressed)
{
    const int item = scanTop + row;
    if (item == 0) {
        drawRow(row, "Type SSID...", pressed, false);
        return;
    }
    if ((item == 1) && (numScan < 1)) {
        drawRowMsg(row, ((numScan == -1) ? "Scanning..." : ((numScan == -2) ? "Scan failed" : "No networks found")));
        return;
    }
    if (item >= scanNumItems()) {
        drawRow(row, nullptr, false, false);
        return;
    }
    const WifiNet& net = scanNets[item - 1];
    char text[48];
    snprintf(text, sizeof(text), "%-26.26s %4d %s", net.ssid, net.rssi, (net.secure ? "" : "open"));
    drawRow(row, text, pressed, false);
}

/**
 * @brief Draw all scan screen rows
 */
static void scanDrawRows()
{
    for (int row = 0; row < ROWS; row++) {
        scanDrawRow(row, false);
    }
}

/**
 * @brief Start keyboard for WiFi password
 */
static void startWifiPass()
{
    char title[41];
    snprintf(title, sizeof(title), "Password for %s", newSsid);
    startInput(KbMode::WIFI_PASS, title, "", 63);
}

/**
 * @brief Run scan screen action
 */
static void scanAction(const int id)
{
    switch (id) {
        case ID_SCAN_BACK:
            showScreen(Screen::NETS);
            break;
        case ID_SCAN_UP:
            if (scanTop > 0) {
                scanTop = max(0, scanTop - ROWS);
                scanDrawRows();
            }
            break;
        case ID_SCAN_DOWN:
            if ((scanTop + ROWS) < scanNumItems()) {
                scanTop += ROWS;
                scanDrawRows();
            }
            break;
        case ID_SCAN_SCAN:
            numScan = -1;
            scanTop = 0;
            wifiScanStart();
            scanDrawRows();
            break;
        default: {
            const int item = scanTop + id;
            if (item == 0) {
                startInput(KbMode::WIFI_SSID, "SSID", "", 32);
            } else if (item < scanNumItems()) {
                const WifiNet& net = scanNets[item - 1];
                strlcpy(newSsid, net.ssid, sizeof(newSsid));
                if (net.secure) {
                    startWifiPass();
                } else {
                    wifiAddNet(newSsid, "");
                    showScreen(Screen::NETS);
                }
            }
            break;
        }
    }
}

/**
 * @brief Poll scan result
 */
static void scanPoll()
{
    if (numScan != -1) {
        return;
    }
    const int res = wifiScanResult(scanNets, MAX_SCAN);
    if (res != -1) {
        numScan = res;
        scanDrawRows();
    }
}

// =====================================================================================================================

static const char* const BACKEND_BTNS[] = { "Back", "Reconnect" };
static constexpr int BACKEND_NUM_BTNS = 2;
static constexpr int BACKEND_ROWS = 4;  // URL, user, password, certificate check
static constexpr int BACKEND_STATUS_ROW = 5;

/**
 * @brief Draw one backend row
 */
static void backendDrawRow(const int row, const bool pressed)
{
    BackendCfg cfg;
    settingsGetBackend(cfg);
    char text[48];
    switch (row) {
        case 0:
            snprintf(text, sizeof(text), "URL: %s", ((cfg.url[0] != '\0') ? cfg.url : "(not set)"));
            break;
        case 1:
            snprintf(text, sizeof(text), "User: %s", ((cfg.user[0] != '\0') ? cfg.user : "(no auth)"));
            break;
        case 2:
            snprintf(text, sizeof(text), "Password: %s", ((cfg.pass[0] != '\0') ? "****" : "(none)"));
            break;
        case 3:
            snprintf(text, sizeof(text), "Check certificate: %s", (cfg.insecure ? "off" : "on"));
            break;
        default:
            drawRow(row, nullptr, false, false);
            return;
    }
    drawRow(row, text, pressed, false);
}

/**
 * @brief Draw backend status row
 */
static void backendDrawStatus()
{
    char text[48];
    backendText(text, sizeof(text));
    drawRowMsg(BACKEND_STATUS_ROW, text);
}

/**
 * @brief Run backend screen action
 */
static void backendAction(const int id)
{
    BackendCfg cfg;
    settingsGetBackend(cfg);
    switch (id) {
        case ID_BTN + 0:
            showScreen(Screen::DEV_CFG);
            break;
        case ID_BTN + 1:
            backendReconnect();
            break;
        case 0:
            startInput(KbMode::URL, "Backend URL (http:// or https://)", cfg.url, 127);
            break;
        case 1:
            startInput(KbMode::USER, "User (empty: no auth)", cfg.user, 32);
            break;
        case 2:
            startInput(KbMode::PASS, "Password", "", 64);
            break;
        case 3:
            settingsSetInsecure(!cfg.insecure);
            backendDrawRow(3, false);
            break;
    }
}

// =====================================================================================================================

static const char* const DISPLAY_BTNS[] = { "Back" };
static constexpr int DISPLAY_NUM_BTNS = 1;
static constexpr int DISPLAY_ROWS = 6;

// PICK screen state
static PickKind pickKind = PickKind::BRIGHTNESS;
static int pickTop = 0;  // First shown option

/**
 * @brief Draw one display settings row
 */
static void displayDrawRow(const int row, const bool pressed)
{
    char text[48];
    if (row == 0) {
        snprintf(text, sizeof(text), "Dim after: %u s (0 = never)", settingsDimTimeout());
    } else if (row == 1) {
        snprintf(text, sizeof(text), "Dim level: %u %%", settingsDimLevel());
    } else if (row == 2) {
        snprintf(text, sizeof(text), "Screensaver after: %u s (0 = never)", settingsSaverTimeout());
    } else if (row == 3) {
        snprintf(text, sizeof(text), "LED brightness: %s (%u)", ledBrightName(settingsLedBrightness()),
            settingsLedBrightness());
    } else {
        const LedStatus status = ((row == 4) ? LedStatus::RUNNING : LedStatus::WAITING);
        snprintf(text, sizeof(text), "%s: %d %s", ((row == 4) ? "Running" : "Waiting"), ledAnimGet(status),
            ledAnimName(status, ledAnimGet(status)));
    }
    drawRow(row, text, pressed, false);
}

static int pickSelected();

/**
 * @brief Open PICK screen, the page with the selected option
 */
static void startPick(const PickKind kind)
{
    pickKind = kind;
    pickTop = 0;
    pickTop = (max(0, pickSelected()) / ROWS) * ROWS;
    showScreen(Screen::PICK);
}

/**
 * @brief Run display settings action
 */
static void displayAction(const int id)
{
    char num[8];
    switch (id) {
        case ID_BTN:
            showScreen(Screen::MENU);
            break;
        case 0:
            snprintf(num, sizeof(num), "%u", settingsDimTimeout());
            startInput(KbMode::DIM_TIMEOUT, "Dim after seconds (0-3600)", num, 4);
            break;
        case 1:
            snprintf(num, sizeof(num), "%u", settingsDimLevel());
            startInput(KbMode::DIM_LEVEL, "Dim level percent (0-100)", num, 3);
            break;
        case 2:
            snprintf(num, sizeof(num), "%u", settingsSaverTimeout());
            startInput(KbMode::SAVER_TIMEOUT, "Screensaver after seconds (0-3600)", num, 4);
            break;
        case 3:
            startPick(PickKind::BRIGHTNESS);
            break;
        case 4:
            startPick(PickKind::RUNNING);
            break;
        case 5:
            startPick(PickKind::WAITING);
            break;
    }
}

// =====================================================================================================================

static const char* const PICK_BTNS[] = { "Back", "Up", "Down" };
static constexpr int ID_PICK_BACK = ID_BTN + 0;
static constexpr int ID_PICK_UP = ID_BTN + 1;
static constexpr int ID_PICK_DOWN = ID_BTN + 2;

/**
 * @brief Get number of PICK options
 */
static int pickCount()
{
    switch (pickKind) {
        case PickKind::BRIGHTNESS:
            return ledBrightCount();
        case PickKind::RUNNING:
            return ledAnimCount(LedStatus::RUNNING);
        case PickKind::WAITING:
            break;
    }
    return ledAnimCount(LedStatus::WAITING);
}

/**
 * @brief Get number of PICK buttons: [Up] [Down] only if the options need more than one page
 */
static int pickNumBtns()
{
    return ((pickCount() > ROWS) ? 3 : 1);
}

/**
 * @brief Get index of the selected PICK option, -1 if none
 */
static int pickSelected()
{
    if (pickKind == PickKind::BRIGHTNESS) {
        for (int ix = 0; ix < ledBrightCount(); ix++) {
            const char* name = nullptr;
            if (ledBrightOption(ix, name) == settingsLedBrightness()) {
                return ix;
            }
        }
        return -1;
    }
    return ledAnimGet((pickKind == PickKind::RUNNING) ? LedStatus::RUNNING : LedStatus::WAITING);
}

/**
 * @brief Draw one PICK row
 */
static void pickDrawRow(const int row, const bool pressed)
{
    const int item = pickTop + row;
    if (item >= pickCount()) {
        drawRow(row, nullptr, false, false);
        return;
    }
    char text[48];
    if (pickKind == PickKind::BRIGHTNESS) {
        const char* name = nullptr;
        const uint8_t value = ledBrightOption(item, name);
        snprintf(text, sizeof(text), "%s (%u)", name, value);
    } else {
        const LedStatus status = ((pickKind == PickKind::RUNNING) ? LedStatus::RUNNING : LedStatus::WAITING);
        snprintf(text, sizeof(text), "%d %s", item, ledAnimName(status, item));
    }
    drawRow(row, text, pressed, (item == pickSelected()));
}

/**
 * @brief Draw all PICK rows
 */
static void pickDrawRows()
{
    for (int row = 0; row < ROWS; row++) {
        pickDrawRow(row, false);
    }
}

/**
 * @brief Run PICK screen action
 */
static void pickAction(const int id)
{
    switch (id) {
        case ID_PICK_BACK:
            showScreen(Screen::DISP_CFG);
            break;
        case ID_PICK_UP:
            if (pickTop > 0) {
                pickTop = max(0, pickTop - ROWS);
                pickDrawRows();
            }
            break;
        case ID_PICK_DOWN:
            if ((pickTop + ROWS) < pickCount()) {
                pickTop += ROWS;
                pickDrawRows();
            }
            break;
        default: {
            const int item = pickTop + id;
            if (pickKind == PickKind::BRIGHTNESS) {
                const char* name = nullptr;
                settingsSetLedBrightness(ledBrightOption(item, name));
            } else {
                ledAnimSet(((pickKind == PickKind::RUNNING) ? LedStatus::RUNNING : LedStatus::WAITING), item);
            }
            pickDrawRows();
            break;
        }
    }
}

// =====================================================================================================================

/**
 * @brief Parse a number
 *
 * @returns the value, -1 if invalid
 */
static long parseNumber(const char* text)
{
    if (text[0] == '\0') {
        return -1;
    }
    for (const char* ch = text; *ch != '\0'; ch++) {
        if (!isdigit(*ch)) {
            return -1;
        }
    }
    return atol(text);
}

/**
 * @brief Handle keyboard OK
 */
static void kbOk()
{
    const char* text = kbGetText();
    switch (kbMode) {
        case KbMode::WIFI_SSID:
            if ((text[0] == '\0') || (strlen(text) > 32)) {
                kbSetTitle("SSID: 1-32 chars");
                return;
            }
            strlcpy(newSsid, text, sizeof(newSsid));
            startWifiPass();
            break;
        case KbMode::WIFI_PASS:
            if (!wifiPassValid(text)) {
                kbSetTitle("Password: empty or 8-63 chars");
                return;
            }
            wifiAddNet(newSsid, text);
            showScreen(Screen::NETS);
            break;
        case KbMode::URL:
            if (!settingsSetUrl(text)) {
                kbSetTitle("URL: http://... or https://...");
                return;
            }
            showScreen(Screen::BACKEND);
            break;
        case KbMode::USER:
            settingsSetUser(text);
            showScreen(Screen::BACKEND);
            break;
        case KbMode::PASS:
            settingsSetPass(text);
            showScreen(Screen::BACKEND);
            break;
        case KbMode::DIM_TIMEOUT:
            if (!settingsSetDimTimeout(parseNumber(text))) {
                kbSetTitle("Enter 0-3600 seconds");
                return;
            }
            showScreen(Screen::DISP_CFG);
            break;
        case KbMode::DIM_LEVEL:
            if (!settingsSetDimLevel(parseNumber(text))) {
                kbSetTitle("Enter 0-100 percent");
                return;
            }
            showScreen(Screen::DISP_CFG);
            break;
        case KbMode::SAVER_TIMEOUT:
            if (!settingsSetSaverTimeout(parseNumber(text))) {
                kbSetTitle("Enter 0-3600 seconds");
                return;
            }
            showScreen(Screen::DISP_CFG);
            break;
        case KbMode::NAME:
            if (!settingsSetName(text)) {
                kbSetTitle("Only letters, digits, space, - _ .");
                return;
            }
            showScreen(Screen::DEV_CFG);
            break;
    }
}

/**
 * @brief Handle keyboard cancel
 */
static void kbCancel()
{
    switch (kbMode) {
        case KbMode::WIFI_SSID:
        case KbMode::WIFI_PASS:
            showScreen(Screen::SCAN);
            break;
        case KbMode::URL:
        case KbMode::USER:
        case KbMode::PASS:
            showScreen(Screen::BACKEND);
            break;
        case KbMode::DIM_TIMEOUT:
        case KbMode::DIM_LEVEL:
        case KbMode::SAVER_TIMEOUT:
            showScreen(Screen::DISP_CFG);
            break;
        case KbMode::NAME:
            showScreen(Screen::DEV_CFG);
            break;
    }
}

// =====================================================================================================================

static constexpr int ID_CERT_NO = ID_BTN + 0;
static constexpr int ID_CERT_YES = ID_BTN + 1;

/**
 * @brief Draw one certificate button
 */
static void certDrawButton(const int id, const bool pressed)
{
    displayButton((id - ID_BTN) * 160, BTN_Y, 160, BTN_H, ((id == ID_CERT_NO) ? "No" : "Yes"), pressed);
}

/**
 * @brief Draw certificate question
 */
static void certDraw()
{
    auto& lcd = displayLcd();
    lcd.fillScreen(TFT_BLACK);
    lcd.setTextColor(TFT_YELLOW, TFT_BLACK);
    lcd.setCursor(0, 0);
    lcd.print("Backend certificate");
    BackendCfg cfg;
    settingsGetBackend(cfg);
    static const char* const lines[] = {
        "The certificate of the backend",
        "is not trusted:",
        "",
        "",
        "Connect without certificate check?",
        "This is not secure.",
    };
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    for (int ix = 0; ix < 6; ix++) {
        lcd.setCursor(0, 40 + (ix * FONT_H));
        lcd.print((ix == 2) ? cfg.url : lines[ix]);
    }
    certDrawButton(ID_CERT_NO, false);
    certDrawButton(ID_CERT_YES, false);
}

// =====================================================================================================================

static constexpr int ID_OTA_LATER = ID_BTN + 0;
static constexpr int ID_OTA_INSTALL = ID_BTN + 1;
static constexpr int ID_OTA_OK = ID_BTN + 0;
static constexpr int OTA_BAR_Y = 112;
static OtaState otaShown = OtaState::NONE;  // State drawn on OTA_RUN

/**
 * @brief Draw one update question button
 */
static void otaAskButton(const int id, const bool pressed)
{
    displayButton((id - ID_BTN) * 160, BTN_Y, 160, BTN_H, ((id == ID_OTA_LATER) ? "Later" : "Install"), pressed);
}

/**
 * @brief Draw update question
 */
static void otaAskDraw()
{
    auto& lcd = displayLcd();
    const OtaStatus ota = backendGetOta();
    lcd.fillScreen(TFT_BLACK);
    lcd.setTextColor(TFT_YELLOW, TFT_BLACK);
    lcd.setCursor(0, 0);
    lcd.print("Firmware update");
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    lcd.setCursor(0, 40);
    lcd.print("A new firmware is available.");
    // Name and version on two lines each
    lcd.setCursor(0, 72);
    lcd.printf("Now: %s", FW_NAME);
    lcd.setCursor(0, 88);
    lcd.printf("     %s", FW_VERSION);
    lcd.setCursor(0, 104);
    lcd.printf("New: %s", FW_NAME);
    lcd.setCursor(0, 120);
    lcd.printf("     %s", ota.version);
    lcd.setCursor(0, 152);
    lcd.print("Install now? The device restarts.");
    otaAskButton(ID_OTA_LATER, false);
    otaAskButton(ID_OTA_INSTALL, false);
}

/**
 * @brief Draw update progress screen
 */
static void otaRunDraw()
{
    auto& lcd = displayLcd();
    const OtaStatus ota = backendGetOta();
    lcd.fillScreen(TFT_BLACK);
    lcd.setTextColor(TFT_YELLOW, TFT_BLACK);
    lcd.setCursor(0, 0);
    lcd.print("Firmware update");
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    lcd.setCursor(0, 40);
    lcd.printf("Installing %s", FW_NAME);
    lcd.setCursor(0, 56);
    lcd.printf("           %s", ota.version);
    lcd.drawRect(10, OTA_BAR_Y, 300, 20, TFT_WHITE);
    otaShown = OtaState::NONE;
}

/**
 * @brief Update progress screen
 */
static void otaRunValues()
{
    auto& lcd = displayLcd();
    const OtaStatus ota = backendGetOta();
    const int w = (296 * constrain(ota.progress, 0, 100)) / 100;
    lcd.fillRect(12, OTA_BAR_Y + 2, w, 16, TFT_GREEN);
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    lcd.setCursor(144, OTA_BAR_Y + 28);
    lcd.printf("%3d %%", ota.progress);
    if (ota.state == otaShown) {
        return;
    }
    otaShown = ota.state;
    lcd.setCursor(0, 168);
    if (ota.state == OtaState::FAILED) {
        lcd.setTextColor(TFT_RED, TFT_BLACK);
        lcd.printf("Failed: %-31.31s", ota.error);
        displayButton(0, BTN_Y, 80, BTN_H, "OK", false);
    } else if (ota.state == OtaState::DONE) {
        lcd.setTextColor(TFT_GREEN, TFT_BLACK);
        lcd.print("Done, restarting...");
    }
}

// =====================================================================================================================

static const char* const LEDTEST_BTNS[] = { "Back", "", "", "" };
static constexpr int LEDTEST_NUM_BTNS = 4;
static constexpr int ID_LEDTEST_BACK = ID_BTN + 0;
static constexpr int ID_LEDTEST_PREV = ID_BTN + 1;
static constexpr int ID_LEDTEST_PLAY = ID_BTN + 2;
static constexpr int ID_LEDTEST_NEXT = ID_BTN + 3;
static constexpr int LEDTEST_DISC_Y = 132;  // LED disc centre

/**
 * @brief Draw LED test button with icon
 */
static void ledTestButton(const int id, const bool pressed)
{
    auto& lcd = displayLcd();
    drawFrameButton(LEDTEST_BTNS, LEDTEST_NUM_BTNS, id, pressed);
    const int w = btnWidth(LEDTEST_NUM_BTNS);
    const int cx = ((id - ID_BTN) * w) + (w / 2);
    const int cy = BTN_Y + (BTN_H / 2);
    const uint16_t fg = (pressed ? TFT_BLACK : TFT_WHITE);
    switch (id) {
        case ID_LEDTEST_PREV:
            lcd.fillRect(cx - 9, cy - 7, 3, 15, fg);
            lcd.fillTriangle(cx - 5, cy, cx + 7, cy - 7, cx + 7, cy + 7, fg);
            break;
        case ID_LEDTEST_PLAY:
            if (ledTestPlaying()) {
                lcd.fillRect(cx - 7, cy - 8, 5, 17, fg);
                lcd.fillRect(cx + 2, cy - 8, 5, 17, fg);
            } else {
                lcd.fillTriangle(cx - 6, cy - 8, cx - 6, cy + 8, cx + 8, cy, fg);
            }
            break;
        case ID_LEDTEST_NEXT:
            lcd.fillTriangle(cx + 5, cy, cx - 7, cy - 7, cx - 7, cy + 7, fg);
            lcd.fillRect(cx + 7, cy - 7, 3, 15, fg);
            break;
        default:
            break;
    }
}

/**
 * @brief Draw LED test screen
 */
static void ledTestDraw()
{
    drawFrame("LED test", LEDTEST_BTNS, LEDTEST_NUM_BTNS);
    for (int id = ID_LEDTEST_PREV; id <= ID_LEDTEST_NEXT; id++) {
        ledTestButton(id, false);
    }
}

/**
 * @brief Draw LED colours as discs with numbers
 *
 * @param[in] leds  LED colours
 * @param[in] num   Number of LEDs
 * @param[in] y     Disc centre
 */
static void drawLedDiscs(const CRGB* leds, const int num, const int y)
{
    auto& lcd = displayLcd();
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    const int space = 320 / max(num, 1);
    const int r = min(12, (space / 2) - 2);
    for (int ix = 0; ix < num; ix++) {
        const int cx = (ix * space) + (space / 2);
        lcd.fillCircle(cx, y, r, lcd.color565(leds[ix].r, leds[ix].g, leds[ix].b));
        lcd.drawCircle(cx, y, r, TFT_DARKGREY);
        lcd.setCursor(cx - (((ix + 1) < 10) ? (FONT_W / 2) : FONT_W), y + r + 4);
        lcd.print(ix + 1);
    }
}

/**
 * @brief Update LED test values
 */
static void ledTestValues(const CRGB* leds, const int num, const uint32_t currentMa, const uint8_t scale)
{
    auto& lcd = displayLcd();
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    lcd.setCursor(0, 28);
    lcd.printf("Step %2d/%2d  %-6s", ledTestIndex() + 1, ledTestNumSteps(), (ledTestPlaying() ? "play" : "pause"));
    char name[40];
    ledTestName(name, sizeof(name));
    lcd.setTextColor(TFT_YELLOW, TFT_BLACK);
    lcd.setCursor(0, 52);
    lcd.printf("%-40.40s", name);
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    lcd.setCursor(0, 76);
    lcd.printf("Current %3u mA  Scale %3u", (unsigned)currentMa, (unsigned)scale);
    drawLedDiscs(leds, num, LEDTEST_DISC_Y);
}

/**
 * @brief Run LED test action
 */
static void ledTestAction(const int id)
{
    switch (id) {
        case ID_LEDTEST_BACK:
            showScreen(Screen::TEST);
            break;
        case ID_LEDTEST_PREV:
            ledTestStep(-1);
            break;
        case ID_LEDTEST_PLAY:
            ledTestPlay(!ledTestPlaying());
            ledTestButton(ID_LEDTEST_PLAY, false);
            break;
        case ID_LEDTEST_NEXT:
            ledTestStep(1);
            break;
    }
}

// =====================================================================================================================

static const char* const DEMO_BTNS[] = { "Back" };
static constexpr int DEMO_NUM_BTNS = 1;

/**
 * @brief Update rainbow demo values
 */
static void demoValues(const CRGB* leds, const int num, const uint32_t currentMa, const uint8_t scale)
{
    auto& lcd = displayLcd();
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    lcd.setCursor(0, 28);
    lcd.print("Back: LEDs show job status again.");
    lcd.setCursor(0, 76);
    lcd.printf("Current %3u mA  Scale %3u", (unsigned)currentMa, (unsigned)scale);
    drawLedDiscs(leds, num, LEDTEST_DISC_Y);
}

// =====================================================================================================================

static const char* const INFO_BTNS[] = { "Back", "Prev", "Next" };
static constexpr int INFO_NUM_BTNS = 3;
static constexpr int ID_INFO_BACK = ID_BTN + 0;
static constexpr int ID_INFO_PREV = ID_BTN + 1;
static constexpr int ID_INFO_NEXT = ID_BTN + 2;

/**
 * @brief Draw a name and value line pair
 */
static void infoLine(const int y, const char* name, const char* value)
{
    auto& lcd = displayLcd();
    lcd.setTextColor(TFT_DARKGREY, TFT_BLACK);
    lcd.setCursor(0, y);
    lcd.printf("%-40s", name);
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    lcd.setCursor(0, y + FONT_H);
    lcd.printf("%-40.40s", value);
}

/**
 * @brief Update LED info values
 */
static void infoValues(const CRGB* leds, const int num)
{
    auto& lcd = displayLcd();
    static LedInfo info;
    const bool valid = backendGetLed(infoLed, info);
    char text[48];

    // Title: LED, colour, label
    lcd.setTextColor(TFT_YELLOW, TFT_BLACK);
    lcd.setCursor(0, 0);
    snprintf(text, sizeof(text), "LED %d  %s", infoLed + 1, ((valid && info.used) ? info.label : ""));
    lcd.printf("%-34.34s", text);
    if (infoLed < num) {
        const CRGB& led = leds[infoLed];
        lcd.fillCircle(320 - DISC_R - 2, (FONT_H / 2), DISC_R, lcd.color565(led.r, led.g, led.b));
        lcd.drawCircle(320 - DISC_R - 2, (FONT_H / 2), DISC_R, TFT_DARKGREY);
    }

    if (!valid || !info.used) {
        lcd.setTextColor(TFT_WHITE, TFT_BLACK);
        lcd.setCursor(0, 24);
        lcd.printf("%-40s", (valid ? "No job set. Set it on the backend" : "No state from the backend yet."));
        lcd.setCursor(0, 40);
        lcd.printf("%-40s", (valid ? "web page, device:" : ""));
        lcd.setCursor(0, 60);
        lcd.printf("%-40s", (valid ? deviceId() : ""));
        lcd.fillRect(0, 76, 320, BTN_Y - 76, TFT_BLACK);
        return;
    }

    // Source and path, up to 3 lines
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    snprintf(text, sizeof(text), "%s:", info.source);
    lcd.setCursor(0, 20);
    lcd.printf("%-40.40s", text);
    const int pathLen = strlen(info.path);
    for (int line = 0; line < 3; line++) {
        lcd.setCursor(0, 36 + (line * FONT_H));
        lcd.printf("%-40.40s", (((line * 40) < pathLen) ? &info.path[line * 40] : ""));
    }

    // Status, result, last change
    static const char* const lights[] = { "blink", "steady", "slow pulse", "fast pulse" };
    lcd.setCursor(0, 88);
    snprintf(text, sizeof(text), "Status   %s%s%s (%s)", ledStatusName(info.status),
        ((info.detail[0] != '\0') ? ", " : ""), info.detail, lights[(int)info.status]);
    lcd.printf("%-40.40s", text);
    lcd.setCursor(0, 104);
    lcd.print("Result   ");
    lcd.setTextColor(resultColour(info.result), TFT_BLACK);
    lcd.printf("%-31s", ledResultName(info.result));
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    lcd.setCursor(0, 120);
    int64_t nowMs = 0;
    if ((info.time > 0.0) && backendGetUnixMs(nowMs)) {
        const int64_t age = max((int64_t)0, (nowMs / 1000) - (int64_t)info.time);
        snprintf(text, sizeof(text), "Updated  %lld %s ago",
            (long long)((age < 60) ? age : ((age < 3600) ? (age / 60) : ((age < 86400) ? (age / 3600) : (age / 86400)))),
            ((age < 60) ? "s" : ((age < 3600) ? "min" : ((age < 86400) ? "h" : "d"))));
    } else {
        snprintf(text, sizeof(text), "Updated  -");
    }
    lcd.printf("%-40.40s", text);

    // Info lines of the source
    for (int ix = 0; ix < 4; ix++) {
        lcd.setCursor(0, 136 + (ix * FONT_H));
        if (ix < info.numInfo) {
            snprintf(text, sizeof(text), "%-8.8s %s", info.info[ix].name, info.info[ix].value);
        } else {
            text[0] = '\0';
        }
        lcd.printf("%-40.40s", text);
    }
}

/**
 * @brief Run LED info action
 */
static void infoAction(const int id)
{
    switch (id) {
        case ID_INFO_BACK:
            showScreen(Screen::MAIN);
            break;
        case ID_INFO_PREV:
            infoLed = (infoLed + LED_NUM - 1) % LED_NUM;
            needRedraw = true;
            break;
        case ID_INFO_NEXT:
            infoLed = (infoLed + 1) % LED_NUM;
            needRedraw = true;
            break;
    }
}

// =====================================================================================================================

/**
 * @brief Find target at position
 *
 * @returns the target ID, -1 for none
 */
static int hitTarget(const int x, const int y)
{
    switch (screen) {
        case Screen::MAIN:
            if ((x >= (MENU_X - 6)) && (y >= (MENU_Y - 6))) {
                return ID_MENU;
            }
            if ((y >= LEDS_Y) && (y < (LEDS_Y + (LED_NUM * FONT_H)))) {
                return (ID_LED + ((y - LEDS_Y) / FONT_H));
            }
            return -1;
        case Screen::MENU:
            return hitList(x, y, MENU_NUM_BTNS, MENU_ROWS);
        case Screen::DEV_CFG:
            return hitList(x, y, DEVICE_NUM_BTNS, DEVICE_ROWS);
        case Screen::TEST:
            return hitList(x, y, TEST_NUM_BTNS, TEST_ROWS);
        case Screen::NETS:
            return hitList(x, y, NETS_NUM_BTNS, wifiNumNets());
        case Screen::SCAN:
            return hitList(x, y, SCAN_NUM_BTNS, scanNumItems() - scanTop);
        case Screen::BACKEND:
            return hitList(x, y, BACKEND_NUM_BTNS, BACKEND_ROWS);
        case Screen::DISP_CFG:
            return hitList(x, y, DISPLAY_NUM_BTNS, DISPLAY_ROWS);
        case Screen::PICK:
            return hitList(x, y, pickNumBtns(), pickCount() - pickTop);
        case Screen::SAVER:
            return -1;
        case Screen::KEYBOARD:
            return kbHitKey(x, y);
        case Screen::CERT:
            return ((y >= BTN_Y) ? ((x < 160) ? ID_CERT_NO : ID_CERT_YES) : -1);
        case Screen::LOGO:
            return (splashDone ? 0 : -1);
        case Screen::OTA_ASK:
            return ((y >= BTN_Y) ? ((x < 160) ? ID_OTA_LATER : ID_OTA_INSTALL) : -1);
        case Screen::OTA_RUN:
            return (((backendGetOta().state == OtaState::FAILED) && (y >= BTN_Y) && (x < 80)) ? ID_OTA_OK : -1);
        case Screen::LED_TEST:
            return hitList(x, y, LEDTEST_NUM_BTNS, 0);
        case Screen::DEMO:
            return hitList(x, y, DEMO_NUM_BTNS, 0);
        case Screen::LED_INFO:
            return hitList(x, y, INFO_NUM_BTNS, 0);
        case Screen::MELODY:
            return hitList(x, y, MELODY_NUM_BTNS, rtttlCount() - melodyTop);
    }
    return -1;
}

/**
 * @brief Draw target normal or pressed
 */
static void drawTarget(const int id, const bool pressed)
{
    if (id < 0) {
        return;
    }
    const bool isBtn = (id >= ID_BTN);
    switch (screen) {
        case Screen::MAIN:
            // LED rows: drawn with the values (pressId)
            if (id == ID_MENU) {
                drawMenuButton(pressed);
            }
            break;
        case Screen::MENU:
            if (isBtn) {
                drawFrameButton(MENU_BTNS, MENU_NUM_BTNS, id, pressed);
            } else {
                menuDrawRow(id, pressed);
            }
            break;
        case Screen::DEV_CFG:
            if (isBtn) {
                drawFrameButton(DEVICE_BTNS, DEVICE_NUM_BTNS, id, pressed);
            } else {
                deviceDrawRow(id, pressed);
            }
            break;
        case Screen::TEST:
            if (isBtn) {
                drawFrameButton(TEST_BTNS, TEST_NUM_BTNS, id, pressed);
            } else {
                testDrawRow(id, pressed);
            }
            break;
        case Screen::NETS:
            if (isBtn) {
                drawFrameButton(NETS_BTNS, NETS_NUM_BTNS, id, pressed);
            } else {
                netsDrawRow(id, pressed);
            }
            break;
        case Screen::SCAN:
            if (isBtn) {
                drawFrameButton(SCAN_BTNS, SCAN_NUM_BTNS, id, pressed);
            } else {
                scanDrawRow(id, pressed);
            }
            break;
        case Screen::BACKEND:
            if (isBtn) {
                drawFrameButton(BACKEND_BTNS, BACKEND_NUM_BTNS, id, pressed);
            } else {
                backendDrawRow(id, pressed);
            }
            break;
        case Screen::DISP_CFG:
            if (isBtn) {
                drawFrameButton(DISPLAY_BTNS, DISPLAY_NUM_BTNS, id, pressed);
            } else {
                displayDrawRow(id, pressed);
            }
            break;
        case Screen::PICK:
            if (isBtn) {
                drawFrameButton(PICK_BTNS, pickNumBtns(), id, pressed);
            } else {
                pickDrawRow(id, pressed);
            }
            break;
        case Screen::MELODY:
            if (isBtn) {
                drawFrameButton(MELODY_BTNS, MELODY_NUM_BTNS, id, pressed);
            } else {
                melodyDrawRow(id, pressed);
            }
            break;
        case Screen::KEYBOARD:
            kbDrawKey(id, pressed);
            break;
        case Screen::CERT:
            certDrawButton(id, pressed);
            break;
        case Screen::LOGO:
        case Screen::SAVER:
            break;
        case Screen::OTA_ASK:
            otaAskButton(id, pressed);
            break;
        case Screen::OTA_RUN:
            displayButton(0, BTN_Y, 80, BTN_H, "OK", pressed);
            break;
        case Screen::LED_TEST:
            ledTestButton(id, pressed);
            break;
        case Screen::DEMO:
            drawFrameButton(DEMO_BTNS, DEMO_NUM_BTNS, id, pressed);
            break;
        case Screen::LED_INFO:
            drawFrameButton(INFO_BTNS, INFO_NUM_BTNS, id, pressed);
            break;
    }
}

/**
 * @brief Run target action
 */
static void runTarget(const int id)
{
    switch (screen) {
        case Screen::MAIN:
            if (id == ID_MENU) {
                showScreen(Screen::MENU);
            } else {
                infoLed = id - ID_LED;
                showScreen(Screen::LED_INFO);
            }
            break;
        case Screen::MENU:
            menuAction(id);
            break;
        case Screen::DEV_CFG:
            deviceAction(id);
            break;
        case Screen::TEST:
            testAction(id);
            break;
        case Screen::NETS:
            netsAction(id);
            break;
        case Screen::SCAN:
            scanAction(id);
            break;
        case Screen::BACKEND:
            backendAction(id);
            break;
        case Screen::DISP_CFG:
            displayAction(id);
            break;
        case Screen::PICK:
            pickAction(id);
            break;
        case Screen::MELODY:
            melodyAction(id);
            break;
        case Screen::KEYBOARD:
            switch (kbPressKey(id)) {
                case KbResult::OK:
                    kbOk();
                    break;
                case KbResult::CANCEL:
                    kbCancel();
                    break;
                case KbResult::NONE:
                    break;
            }
            break;
        case Screen::CERT:
            if (id == ID_CERT_YES) {
                settingsSetInsecure(true);
            }
            showScreen(Screen::MAIN);
            break;
        case Screen::LOGO:
            showScreen(Screen::MENU);
            break;
        case Screen::OTA_ASK:
            if (id == ID_OTA_INSTALL) {
                backendOtaStart();
                showScreen(Screen::OTA_RUN);
            } else {
                showScreen(Screen::MAIN);
            }
            break;
        case Screen::OTA_RUN:
            showScreen(Screen::MAIN);
            break;
        case Screen::LED_TEST:
            ledTestAction(id);
            break;
        case Screen::DEMO:
            showScreen(Screen::TEST);
            break;
        case Screen::LED_INFO:
            infoAction(id);
            break;
    }
}

/**
 * @brief Handle touch down and release
 */
static void handleTouch()
{
    int x = 0;
    int y = 0;
    const bool down = displayTouch(x, y);

    if (down) {
        releaseCnt = 0;
        lastX = x;
        lastY = y;
        bool woke = displayActivity();
        lastInput = millis();
        if (!touchDown && (screen == Screen::SAVER)) {
            showScreen(Screen::MAIN);
            woke = true;
        }
        if (!touchDown) {
            touchDown = true;
            touchWake = woke;
            pressId = (touchWake ? -1 : hitTarget(x, y));
            drawTarget(pressId, true);
        }
    } else if (touchDown) {
        // Release after a few free reads
        releaseCnt++;
        if (releaseCnt >= RELEASE_COUNT) {
            touchDown = false;
            const int id = pressId;
            pressId = -1;
            drawTarget(id, false);
            if ((id >= 0) && (hitTarget(lastX, lastY) == id)) {
                runTarget(id);
            }
        }
    }
}

/**
 * @brief Draw the logo full screen
 */
static void drawLogo()
{
    auto& lcd = displayLcd();
    lcd.fillScreen(TFT_BLACK);
    if (!lcd.drawPng(logoStart, logoEnd - logoStart, 0, 0, lcd.width(), lcd.height())) {
        Serial.println("Logo: PNG decode failed");
    }
}

/**
 * @brief Draw device ID and name on the logo
 */
static void drawLogoTop()
{
    auto& lcd = displayLcd();
    lcd.setTextColor(TFT_WHITE);  // Transparent background
    lcd.setCursor(9, 9);
    lcd.print(deviceId());
    char name[DEVICE_NAME_MAX + 1];
    settingsGetName(name, sizeof(name));
    lcd.setCursor(lcd.width() - 9 - (displayTextLen(name) * FONT_W), 9);
    lcd.print(name);
}

// Screensaver date and time: text and position
static constexpr int SAVER_TEXT_W = (19 * FONT_W);
static constexpr int SAVER_X = ((320 - SAVER_TEXT_W) / 2);
static constexpr int SAVER_Y = (240 - FONT_H - 9);

/**
 * @brief Update screensaver date and time (bottom centre, when the second changes)
 */
static void saverValues()
{
    struct tm tm;
    if (!backendGetTime(tm)) {
        return;
    }
    const int second = (((tm.tm_yday * 1440) + (tm.tm_hour * 60) + tm.tm_min) * 60) + tm.tm_sec;
    if (second == saverSecond) {
        return;
    }
    saverSecond = second;
    char text[24];
    strftime(text, sizeof(text), "%Y-%m-%d %H:%M:%S", &tm);
    auto& lcd = displayLcd();

    // Logo again below the text (the text has no background)
    if (saverBg.getBuffer() != nullptr) {
        saverBg.pushSprite(&lcd, SAVER_X - 2, SAVER_Y);
    } else {
        lcd.setClipRect(SAVER_X - 2, SAVER_Y, SAVER_TEXT_W + 4, FONT_H);
        lcd.drawPng(logoStart, logoEnd - logoStart, 0, 0, lcd.width(), lcd.height());
        lcd.clearClipRect();
    }
    lcd.setTextColor(TFT_WHITE);
    lcd.setCursor(SAVER_X, SAVER_Y);
    lcd.print(text);
}

/**
 * @brief Draw screensaver (logo, device ID and name, date and time)
 */
static void saverDraw()
{
    auto& lcd = displayLcd();
    drawLogo();
    drawLogoTop();

    // Keep the logo below the time (a full PNG decode is slow)
    if ((saverBg.getBuffer() == nullptr) && (saverBg.createSprite(SAVER_TEXT_W + 4, FONT_H) == nullptr)) {
        Serial.println("UI: no memory for screensaver");
    }
    if (saverBg.getBuffer() != nullptr) {
        saverBg.drawPng(logoStart, logoEnd - logoStart, -(SAVER_X - 2), -SAVER_Y, lcd.width(), lcd.height());
    }
    saverSecond = -1;
    saverValues();
}

/**
 * @brief Get signature of the LED status and result of all LEDs (0 if no state yet)
 */
static uint32_t ledSignature()
{
    LedState states[LED_NUM];
    if (!backendGetLedStates(states, LED_NUM)) {
        return 0;
    }
    uint32_t sig = 2166136261u;  // FNV-1a
    for (const LedState& state : states) {
        const uint8_t val = (uint8_t)((state.used ? 0x80 : 0) | ((int)state.status << 3) | (int)state.result);
        sig = (sig ^ val) * 16777619u;
    }
    return sig;
}

/**
 * @brief Start and end the screensaver
 */
static void saverUpdate()
{
    // LED status or result change: back to the main screen, timeout starts again
    static uint32_t lastCheck = 0;
    static uint32_t lastSig = 0;
    const uint32_t now = millis();
    if ((now - lastCheck) >= 250) {
        lastCheck = now;
        const uint32_t sig = ledSignature();
        if (sig != lastSig) {
            lastSig = sig;
            lastChange = now;
            if (screen == Screen::SAVER) {
                Serial.println("UI: screensaver off (LED change)");
                showScreen(Screen::MAIN);
            }
        }
    }

    // Main screen without touch and change for the timeout: screensaver
    const uint32_t timeoutMs = (uint32_t)settingsSaverTimeout() * 1000;
    if ((screen == Screen::MAIN) && (timeoutMs > 0) && !touchDown && ((now - lastInput) >= timeoutMs) &&
        ((now - lastChange) >= timeoutMs)) {
        Serial.println("UI: screensaver on");
        showScreen(Screen::SAVER);
    }
}

/**
 * @brief Draw full screen
 */
static void drawScreen()
{
    switch (screen) {
        case Screen::MAIN:
            mainDraw();
            break;
        case Screen::MENU:
            drawFrame("Settings", MENU_BTNS, MENU_NUM_BTNS);
            for (int row = 0; row < MENU_ROWS; row++) {
                menuDrawRow(row, false);
            }
            break;
        case Screen::DEV_CFG:
            drawFrame("Device", DEVICE_BTNS, DEVICE_NUM_BTNS);
            for (int row = 0; row < DEVICE_ROWS; row++) {
                deviceDrawRow(row, false);
            }
            break;
        case Screen::TEST:
            drawFrame("Test", TEST_BTNS, TEST_NUM_BTNS);
            for (int row = 0; row < TEST_ROWS; row++) {
                testDrawRow(row, false);
            }
            break;
        case Screen::NETS:
            drawFrame("WiFi networks (tried in order)", NETS_BTNS, NETS_NUM_BTNS);
            netsDrawRows();
            break;
        case Screen::SCAN:
            drawFrame("Add network", SCAN_BTNS, SCAN_NUM_BTNS);
            scanDrawRows();
            break;
        case Screen::BACKEND:
            drawFrame("Backend", BACKEND_BTNS, BACKEND_NUM_BTNS);
            for (int row = 0; row < BACKEND_ROWS; row++) {
                backendDrawRow(row, false);
            }
            backendDrawStatus();
            break;
        case Screen::DISP_CFG:
            drawFrame("Display", DISPLAY_BTNS, DISPLAY_NUM_BTNS);
            for (int row = 0; row < DISPLAY_ROWS; row++) {
                displayDrawRow(row, false);
            }
            break;
        case Screen::PICK:
            drawFrame(((pickKind == PickKind::BRIGHTNESS) ? "LED brightness" :
                ((pickKind == PickKind::RUNNING) ? "LED animation: running" : "LED animation: waiting")),
                PICK_BTNS, pickNumBtns());
            pickDrawRows();
            break;
        case Screen::MELODY:
            drawFrame("Melodies (tap to play)", MELODY_BTNS, MELODY_NUM_BTNS);
            melodyDrawRows();
            break;
        case Screen::KEYBOARD:
            kbDraw();
            break;
        case Screen::CERT:
            certDraw();
            break;
        case Screen::SAVER:
            saverDraw();
            break;
        case Screen::LOGO: {
            auto& lcd = displayLcd();
            drawLogo();
            drawLogoTop();
            lcd.setCursor(9, lcd.height() - (2 * FONT_H) - 9);
            lcd.print(FW_NAME);
            lcd.setCursor(9, lcd.height() - FONT_H - 9);
            lcd.print(FW_VERSION);
            logoTime = millis();
            break;
        }
        case Screen::OTA_ASK:
            otaAskDraw();
            break;
        case Screen::OTA_RUN:
            otaRunDraw();
            break;
        case Screen::LED_TEST:
            ledTestDraw();
            break;
        case Screen::DEMO:
            drawFrame("Rainbow demo", DEMO_BTNS, DEMO_NUM_BTNS);
            break;
        case Screen::LED_INFO:
            drawFrame("", INFO_BTNS, INFO_NUM_BTNS);
            break;
    }
}

// =====================================================================================================================

void uiUpdate(const CRGB* leds, const int num, const uint32_t currentMa, const uint8_t scale)
{
    handleTouch();
    displayDimUpdate();

    // Screensaver: on and off
    saverUpdate();

    // Ask once about untrusted certificate
    const bool onMain = ((screen == Screen::MAIN) || (screen == Screen::SAVER));
    if (onMain && !touchDown && backendCertAsk()) {
        displayActivity();
        showScreen(Screen::CERT);
    }

    // Ask once about new firmware
    if (onMain && !touchDown && backendOtaAsk()) {
        displayActivity();
        showScreen(Screen::OTA_ASK);
    }

    // Update started from shell: show progress
    if ((screen != Screen::OTA_RUN) && (backendGetOta().state == OtaState::RUNNING)) {
        displayActivity();
        showScreen(Screen::OTA_RUN);
    }

    // Display flip changed (any source): draw again
    if (displayFlipUpdate()) {
        needRedraw = true;
    }

    // Synced settings changed (e.g. web page): draw again
    static uint32_t lastRev = settingsRev();
    const uint32_t rev = settingsRev();
    if (rev != lastRev) {
        lastRev = rev;
        if ((screen == Screen::DEV_CFG) || (screen == Screen::DISP_CFG) || (screen == Screen::PICK) ||
            (screen == Screen::SAVER) || ((screen == Screen::LOGO) && splashDone)) {
            needRedraw = true;
        }
    }

    if (needRedraw) {
        needRedraw = false;
        drawScreen();
    }

    // Leave settings after timeout
    const bool splash = ((screen == Screen::LOGO) && !splashDone);
    const bool updating = ((screen == Screen::OTA_RUN) && (backendGetOta().state != OtaState::FAILED));
    if ((screen != Screen::MAIN) && (screen != Screen::SAVER) && !splash && !updating && !touchDown &&
        ((millis() - lastInput) >= UI_MENU_TIMEOUT_MS)) {
        Serial.println("UI: settings timeout");
        wifiResume();
        selNet = -1;
        showScreen(Screen::MAIN);
        drawScreen();
        needRedraw = false;
    }

    // Periodic updates
    static uint32_t lastDraw = 0;
    const uint32_t now = millis();
    switch (screen) {
        case Screen::MAIN:
            if ((now - lastDraw) >= DISP_UPDATE_MS) {
                lastDraw = now;
                mainValues(leds, num);
            }
            break;
        case Screen::NETS:
            // Connected marker, list changes from shell
            if (((now - lastDraw) >= 1000) && !touchDown) {
                lastDraw = now;
                if (selNet >= wifiNumNets()) {
                    selNet = -1;
                }
                netsDrawRows();
            }
            break;
        case Screen::BACKEND:
            if (((now - lastDraw) >= 1000) && !touchDown) {
                lastDraw = now;
                backendDrawStatus();
            }
            break;
        case Screen::SCAN:
            scanPoll();
            break;
        case Screen::LOGO:
            // Logo after start: then main screen
            if (!splashDone && ((now - logoTime) >= LOGO_MS)) {
                splashDone = true;
                showScreen(Screen::MAIN);
            }
            break;
        case Screen::OTA_RUN:
            if ((now - lastDraw) >= 200) {
                lastDraw = now;
                otaRunValues();
            }
            break;
        case Screen::LED_TEST:
            if ((now - lastDraw) >= DISP_UPDATE_MS) {
                lastDraw = now;
                ledTestValues(leds, num, currentMa, scale);
            }
            break;
        case Screen::DEMO:
            if ((now - lastDraw) >= DISP_UPDATE_MS) {
                lastDraw = now;
                demoValues(leds, num, currentMa, scale);
            }
            break;
        case Screen::LED_INFO:
            if ((now - lastDraw) >= DISP_UPDATE_MS) {
                lastDraw = now;
                infoValues(leds, num);
            }
            break;
        case Screen::SAVER:
            if ((now - lastDraw) >= 100) {
                lastDraw = now;
                saverValues();
            }
            break;
        case Screen::MELODY:
            // Melody ended, or started from shell
            if (!touchDown && (melodyPlaying() != melodyShown)) {
                melodyDrawRows();
            }
            break;
        case Screen::MENU:
        case Screen::DEV_CFG:
        case Screen::TEST:
        case Screen::DISP_CFG:
        case Screen::PICK:
        case Screen::KEYBOARD:
        case Screen::CERT:
        case Screen::OTA_ASK:
            break;
    }
}
