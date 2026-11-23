// Display and touch hardware of the CYD
//
// Driver: LovyanGFX, ILI9341 panel on the HSPI bus, XPT2046 touch controller on the VSPI bus. Font: Spleen 8x16
// (monospaced pixel font, BSD-2 licence, from U8g2). In landscape orientation (320x240) this gives 40 columns and
// 15 rows of text.
//
// Flip: the setting "reverse LEDs" turns the picture and the touch by 180 deg, the Chlotz stands with the display at
// the top. LovyanGFX turns the touch with the rotation; the saved touch calibration stays good.
//
// Dimming: after the dim timeout (settings) without touch, the backlight goes to the dim level. A touch restores
// full brightness.
//
// Touch calibration: hold the BOOT button during start to run the LovyanGFX calibration. The result is saved in
// NVS (namespace "touch") and loaded at the next start.

#include <Arduino.h>
#include <Preferences.h>
#include <clib/u8g2.h>

#include "config.hpp"
#include "chlotzsettings.hpp"
#include "display.hpp"
#include "ledsettings.hpp"

// CYD display hardware configuration
class LGFX : public lgfx::LGFX_Device
{
  public:
    LGFX()
    {
        {
            auto cfg = _bus.config();
            cfg.spi_host = HSPI_HOST;
            cfg.spi_mode = 0;
            cfg.freq_write = 40000000;
            cfg.freq_read = 16000000;
            cfg.spi_3wire = false;
            cfg.use_lock = true;
            cfg.dma_channel = SPI_DMA_CH_AUTO;
            cfg.pin_sclk = DISP_SCLK_PIN;
            cfg.pin_mosi = DISP_MOSI_PIN;
            cfg.pin_miso = DISP_MISO_PIN;
            cfg.pin_dc = DISP_DC_PIN;
            _bus.config(cfg);
            _panel.setBus(&_bus);
        }
        {
            auto cfg = _panel.config();
            cfg.pin_cs = DISP_CS_PIN;
            cfg.pin_rst = -1;
            cfg.pin_busy = -1;
            cfg.panel_width = 240;
            cfg.panel_height = 320;
            cfg.offset_x = 0;
            cfg.offset_y = 0;
            cfg.offset_rotation = 0;
            cfg.readable = true;
            cfg.invert = false;
            cfg.rgb_order = false;
            cfg.dlen_16bit = false;
            cfg.bus_shared = false;
            _panel.config(cfg);
        }
        {
            auto cfg = _light.config();
            cfg.pin_bl = DISP_BL_PIN;
            cfg.invert = false;
            cfg.freq = 44100;
            cfg.pwm_channel = 7;
            _light.config(cfg);
            _panel.setLight(&_light);
        }
        {
            auto cfg = _touch.config();
            // Raw ranges measured on this CYD
            cfg.x_min = 270;
            cfg.x_max = 3720;
            cfg.y_min = 3690;
            cfg.y_max = 180;
            cfg.pin_int = TOUCH_IRQ_PIN;
            cfg.bus_shared = false;
            cfg.offset_rotation = 0;
            cfg.spi_host = VSPI_HOST;
            cfg.freq = 1000000;
            cfg.pin_sclk = TOUCH_SCLK_PIN;
            cfg.pin_mosi = TOUCH_MOSI_PIN;
            cfg.pin_miso = TOUCH_MISO_PIN;
            cfg.pin_cs = TOUCH_CS_PIN;
            _touch.config(cfg);
            _panel.setTouch(&_touch);
        }
        setPanel(&_panel);
    }

  private:
    lgfx::Panel_ILI9341 _panel;
    lgfx::Bus_SPI _bus;
    lgfx::Light_PWM _light;
    lgfx::Touch_XPT2046 _touch;
};

static constexpr uint8_t ROTATION_NORMAL = 1;   // Landscape, USB on the left
static constexpr uint8_t ROTATION_FLIPPED = 3;  // Landscape, USB on the right

static LGFX lcd;
const lgfx::U8g2font fontSpleen(u8g2_font_spleen8x16_mf);

static uint32_t lastActivity = 0;
static bool dimmed = false;

/**
 * @brief Load or run touch calibration
 */
static void touchCalibrate()
{
    Preferences prefs;
    prefs.begin("touch", false);
    uint16_t cal[8];

    // Hold BOOT at start to calibrate
    pinMode(TOUCH_CAL_PIN, INPUT_PULLUP);
    if (digitalRead(TOUCH_CAL_PIN) == LOW) {
        lcd.fillScreen(TFT_BLACK);
        lcd.setTextColor(TFT_WHITE, TFT_BLACK);
        lcd.setTextDatum(textdatum_t::middle_center);
        lcd.drawString("Touch the arrows", lcd.width() / 2, lcd.height() / 2);
        lcd.setTextDatum(textdatum_t::top_left);
        lcd.calibrateTouch(cal, TFT_WHITE, TFT_BLACK, 20);
        prefs.putBytes("cal", cal, sizeof(cal));
        Serial.println("Touch: calibration saved");
    } else if (prefs.isKey("cal") && (prefs.getBytesLength("cal") == sizeof(cal))) {
        prefs.getBytes("cal", cal, sizeof(cal));
        lcd.setTouchCalibrate(cal);
        Serial.println("Touch: calibration loaded");
    }
    prefs.end();
}

void displayInit()
{
    lcd.init();
    lcd.setRotation(settingsLedReverse() ? ROTATION_FLIPPED : ROTATION_NORMAL);
    lcd.setBrightness(DISP_BRIGHTNESS);
    lcd.setFont(&fontSpleen);
    touchCalibrate();
    lcd.fillScreen(TFT_BLACK);
    lastActivity = millis();
}

bool displayFlipUpdate()
{
    const uint8_t rotation = (settingsLedReverse() ? ROTATION_FLIPPED : ROTATION_NORMAL);
    if (lcd.getRotation() == rotation) {
        return false;
    }
    lcd.setRotation(rotation);
    lcd.fillScreen(TFT_BLACK);
    return true;
}

int displayTextLen(const char* text)
{
    int len = 0;
    for (; *text != '\0'; text++) {
        if ((*text & 0xc0) != 0x80) {
            len++;
        }
    }
    return len;
}

lgfx::LGFX_Device& displayLcd()
{
    return lcd;
}

bool displayTouch(int& x, int& y)
{
    int32_t tx = 0;
    int32_t ty = 0;
    if (lcd.getTouch(&tx, &ty) > 0) {
        x = tx;
        y = ty;
        return true;
    }
    return false;
}

bool displayActivity()
{
    lastActivity = millis();
    if (dimmed) {
        dimmed = false;
        lcd.setBrightness(DISP_BRIGHTNESS);
        return true;
    }
    return false;
}

void displayDimUpdate()
{
    const uint32_t timeoutMs = (uint32_t)settingsDimTimeout() * 1000;
    if (dimmed || (timeoutMs == 0) || ((millis() - lastActivity) < timeoutMs)) {
        return;
    }
    dimmed = true;
    lcd.setBrightness(((uint32_t)DISP_BRIGHTNESS * settingsDimLevel()) / 100);
}

void displayButton(const int x, const int y, const int w, const int h, const char* label, const bool pressed,
    const bool active)
{
    const uint16_t bg = (pressed ? TFT_WHITE : lcd.color565(48, 48, 48));
    const uint16_t fg = (pressed ? TFT_BLACK : (active ? TFT_YELLOW : TFT_WHITE));
    lcd.fillRect(x, y, w, h, TFT_BLACK);
    lcd.fillRoundRect(x + 1, y + 1, w - 2, h - 2, 4, bg);
    lcd.drawRoundRect(x + 1, y + 1, w - 2, h - 2, 4, TFT_DARKGREY);
    lcd.setTextColor(fg, bg);
    lcd.setTextDatum(textdatum_t::middle_center);
    lcd.drawString(label, x + (w / 2), y + (h / 2));
    lcd.setTextDatum(textdatum_t::top_left);
}
