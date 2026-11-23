// On-screen keyboard
//
// Layout for the 320x240 landscape display:
//
//   y   0: title
//   y  20: input field
//   y  56: 5 rows of keys, 36 px high, 32 px wide
//
//   row 0:   1 2 3 4 5 6 7 8 9 0
//   row 1:   q w e r t y u i o p
//   row 2:   a s d f g h j k l -
//   row 3:   [Shift] z x c v b n m [Del]
//   row 4:   [123] [<] [Space] [>] [Cancel] [OK]
//
// Shift toggles upper case. [123] toggles the symbol layer (all other printable ASCII characters).
//
// The input field shows a block cursor. Keys insert at the cursor, [Del] removes the character before it. [<] and
// [>] move the cursor. A tap into the field puts the cursor there. A tap on the "<" or ">" mark at the field edges
// (shown when the text is longer than the field) moves the cursor one step out of the view, which scrolls it. The
// view keeps some characters around the cursor visible.

#include <Arduino.h>

#include "display.hpp"
#include "keyboard.hpp"

// Layer: 4 rows of character keys
struct Layer
{
    const char* rows[4];
};

static const Layer LAYERS[] = {
    { { "1234567890", "qwertyuiop", "asdfghjkl-", "zxcvbnm" } },
    { { "1234567890", "QWERTYUIOP", "ASDFGHJKL_", "ZXCVBNM" } },
    { { "!@#$%^&*()", "-_=+[]{}\\|", ";:'\",.<>/?", "`~" } },
};
static constexpr int LAYER_LOWER = 0;
static constexpr int LAYER_UPPER = 1;
static constexpr int LAYER_SYMBOL = 2;

// Key types
enum class KeyType
{
    CHAR,
    SHIFT,
    DELETE,
    SYMBOL,
    SPACE,
    CANCEL,
    OK,
    LEFT,
    RIGHT,
    FIELD,  // Input field (tap moves cursor)
};

// One key on the screen
struct Key
{
    int16_t x;
    int16_t y;
    int16_t w;
    int16_t h;
    KeyType type;
    char ch;
};

// Geometry, in pixels
static constexpr int INPUT_Y = 20;
static constexpr int INPUT_H = 28;
static constexpr int KEYS_Y = 56;
static constexpr int KEY_W = 32;
static constexpr int KEY_H = 36;
static constexpr int TEXT_X = 16;     // First character
static constexpr int VIEW_CHARS = 36; // Shown characters
static constexpr int VIEW_MARGIN = 4; // Characters around cursor

static constexpr int MAX_KEYS = 48;
static constexpr int MAX_TEXT = 127;  // Longest input (URL)

static Key keys[MAX_KEYS];
static int numKeys = 0;
static int layer = LAYER_LOWER;
static char title[41] = "";
static char text[MAX_TEXT + 1] = "";
static int maxLen = MAX_TEXT;
static int cursor = 0;     // Insert position (characters)
static int viewStart = 0;  // First shown character
static int hitX = 0;       // Last touch x in field

/**
 * @brief Add one key
 */
static void addKey(const int x, const int y, const int w, const KeyType type, const char ch = '\0',
    const int h = KEY_H)
{
    if (numKeys < MAX_KEYS) {
        keys[numKeys] = { (int16_t)x, (int16_t)y, (int16_t)w, (int16_t)h, type, ch };
        numKeys++;
    }
}

/**
 * @brief Add a row of characters
 *
 * @param[in] chars  Characters of the row
 * @param[in] x0     Left edge of row area
 * @param[in] width  Width of row area
 * @param[in] y      Top edge
 */
static void addCharRow(const char* chars, const int x0, const int width, const int y)
{
    const int num = strlen(chars);
    const int x = x0 + ((width - (num * KEY_W)) / 2);
    for (int ix = 0; ix < num; ix++) {
        addKey(x + (ix * KEY_W), y, KEY_W, KeyType::CHAR, chars[ix]);
    }
}

/**
 * @brief Build keys of current layer
 */
static void buildKeys()
{
    numKeys = 0;
    const Layer& lay = LAYERS[layer];
    for (int row = 0; row < 3; row++) {
        addCharRow(lay.rows[row], 0, 320, KEYS_Y + (row * KEY_H));
    }
    const int y3 = KEYS_Y + (3 * KEY_H);
    addKey(0, y3, 48, KeyType::SHIFT);
    addCharRow(lay.rows[3], 48, 224, y3);
    addKey(272, y3, 48, KeyType::DELETE);
    const int y4 = KEYS_Y + (4 * KEY_H);
    addKey(0, y4, 48, KeyType::SYMBOL);
    addKey(48, y4, 40, KeyType::LEFT);
    addKey(88, y4, 64, KeyType::SPACE);
    addKey(152, y4, 40, KeyType::RIGHT);
    addKey(192, y4, 64, KeyType::CANCEL);
    addKey(256, y4, 64, KeyType::OK);
    addKey(0, INPUT_Y, 320, KeyType::FIELD, '\0', INPUT_H);
}

/**
 * @brief Get byte offset of character
 *
 * @param[in] charIx  Character index in the text (UTF-8)
 *
 * @returns the offset of that character, the text length if after the end
 */
static int byteOffset(const int charIx)
{
    int offs = 0;
    for (int ix = 0; (ix < charIx) && (text[offs] != '\0'); ix++) {
        do {
            offs++;
        } while ((text[offs] & 0xc0) == 0x80);
    }
    return offs;
}

/**
 * @brief Scroll view to cursor
 */
static void scrollView()
{
    const int len = displayTextLen(text);
    // Cursor can be after the last character
    const int maxStart = max(0, (len + 1) - VIEW_CHARS);
    const int margin = min(VIEW_MARGIN, (VIEW_CHARS - 1) / 2);
    if (cursor < (viewStart + margin)) {
        viewStart = cursor - margin;
    } else if (cursor > (viewStart + (VIEW_CHARS - 1 - margin))) {
        viewStart = cursor - (VIEW_CHARS - 1 - margin);
    }
    viewStart = constrain(viewStart, 0, maxStart);
}

/**
 * @brief Draw input field
 */
static void drawInput()
{
    scrollView();
    auto& lcd = displayLcd();
    const int len = displayTextLen(text);
    const int y = INPUT_Y + ((INPUT_H - FONT_H) / 2);

    // Marks for text outside the view
    lcd.setTextColor(TFT_DARKGREY, TFT_BLACK);
    lcd.setCursor(4, y);
    lcd.print((viewStart > 0) ? '<' : ' ');
    lcd.setCursor(TEXT_X + (VIEW_CHARS * FONT_W), y);
    lcd.print(((viewStart + VIEW_CHARS) <= len) ? '>' : ' ');

    // Text with block cursor
    lcd.setCursor(TEXT_X, y);
    for (int ix = viewStart; ix < (viewStart + VIEW_CHARS); ix++) {
        char ch[5] = " ";
        if (ix < len) {
            const int offs = byteOffset(ix);
            strlcpy(ch, &text[offs], min((int)sizeof(ch), (byteOffset(ix + 1) - offs) + 1));
        }
        if (ix == cursor) {
            lcd.setTextColor(TFT_BLACK, TFT_WHITE);
        } else {
            lcd.setTextColor(TFT_WHITE, TFT_BLACK);
        }
        lcd.print(ch);
    }
}

/**
 * @brief Draw all keys
 */
static void drawKeys()
{
    displayLcd().fillRect(0, KEYS_Y, 320, 240 - KEYS_Y, TFT_BLACK);
    for (int id = 0; id < numKeys; id++) {
        kbDrawKey(id, false);
    }
}

void kbStart(const char* newTitle, const char* initial, const int newMaxLen)
{
    strlcpy(title, newTitle, sizeof(title));
    maxLen = constrain(newMaxLen, 1, MAX_TEXT);
    strlcpy(text, initial, min((int)sizeof(text), maxLen + 1));
    cursor = displayTextLen(text);
    viewStart = 0;
    layer = LAYER_LOWER;
    buildKeys();
}

/**
 * @brief Draw title line
 */
static void drawTitle()
{
    auto& lcd = displayLcd();
    lcd.setTextColor(TFT_YELLOW, TFT_BLACK);
    lcd.setCursor(0, 0);
    lcd.printf("%-40.40s", title);
}

void kbSetTitle(const char* newTitle)
{
    strlcpy(title, newTitle, sizeof(title));
    drawTitle();
}

void kbDraw()
{
    auto& lcd = displayLcd();
    lcd.fillScreen(TFT_BLACK);
    drawTitle();
    lcd.drawRect(0, INPUT_Y, 320, INPUT_H, TFT_DARKGREY);
    drawKeys();  // Also draws the input field
}

int kbHitKey(const int x, const int y)
{
    for (int id = 0; id < numKeys; id++) {
        const Key& key = keys[id];
        if ((x >= key.x) && (x < (key.x + key.w)) && (y >= key.y) && (y < (key.y + key.h))) {
            if (key.type == KeyType::FIELD) {
                hitX = x;
            }
            return id;
        }
    }
    return -1;
}

void kbDrawKey(const int id, const bool pressed)
{
    if ((id < 0) || (id >= numKeys)) {
        return;
    }
    const Key& key = keys[id];
    char label[8] = "";
    bool active = false;
    switch (key.type) {
        case KeyType::CHAR:
            label[0] = key.ch;
            label[1] = '\0';
            break;
        case KeyType::SHIFT:
            strlcpy(label, "Shift", sizeof(label));
            active = (layer == LAYER_UPPER);
            break;
        case KeyType::DELETE:
            strlcpy(label, "Del", sizeof(label));
            break;
        case KeyType::SYMBOL:
            strlcpy(label, ((layer == LAYER_SYMBOL) ? "abc" : "123"), sizeof(label));
            break;
        case KeyType::SPACE:
            strlcpy(label, "Space", sizeof(label));
            break;
        case KeyType::CANCEL:
            strlcpy(label, "Cancel", sizeof(label));
            break;
        case KeyType::OK:
            strlcpy(label, "OK", sizeof(label));
            break;
        case KeyType::LEFT:
            strlcpy(label, "<", sizeof(label));
            break;
        case KeyType::RIGHT:
            strlcpy(label, ">", sizeof(label));
            break;
        case KeyType::FIELD:
            drawInput();
            return;
    }
    displayButton(key.x, key.y, key.w, key.h, label, pressed, active);
}

KbResult kbPressKey(const int id)
{
    if ((id < 0) || (id >= numKeys)) {
        return KbResult::NONE;
    }
    const int size = strlen(text);
    const int len = displayTextLen(text);
    const int offs = byteOffset(cursor);
    switch (keys[id].type) {
        case KeyType::CHAR:
        case KeyType::SPACE:
            if (size < maxLen) {
                // Move the rest incl. NUL right
                memmove(&text[offs + 1], &text[offs], (size - offs) + 1);
                text[offs] = ((keys[id].type == KeyType::SPACE) ? ' ' : keys[id].ch);
                cursor++;
                drawInput();
            }
            break;
        case KeyType::DELETE:
            if (cursor > 0) {
                const int prev = byteOffset(cursor - 1);
                memmove(&text[prev], &text[offs], (size - offs) + 1);
                cursor--;
                drawInput();
            }
            break;
        case KeyType::LEFT:
            if (cursor > 0) {
                cursor--;
                drawInput();
            }
            break;
        case KeyType::RIGHT:
            if (cursor < len) {
                cursor++;
                drawInput();
            }
            break;
        case KeyType::FIELD: {
            // Left of text: one step left
            const int col = ((hitX < TEXT_X) ? -1 : ((hitX - TEXT_X) / FONT_W));
            cursor = constrain(viewStart + col, 0, len);
            drawInput();
            break;
        }
        case KeyType::SHIFT:
            if (layer != LAYER_SYMBOL) {
                layer = ((layer == LAYER_LOWER) ? LAYER_UPPER : LAYER_LOWER);
                buildKeys();
                drawKeys();
            }
            break;
        case KeyType::SYMBOL:
            layer = ((layer == LAYER_SYMBOL) ? LAYER_LOWER : LAYER_SYMBOL);
            buildKeys();
            drawKeys();
            break;
        case KeyType::CANCEL:
            return KbResult::CANCEL;
        case KeyType::OK:
            return KbResult::OK;
    }
    return KbResult::NONE;
}

const char* kbGetText()
{
    return text;
}
