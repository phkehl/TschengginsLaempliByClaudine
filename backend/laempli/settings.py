"""Device settings: device name, display and LED settings, synced with the device

Each device type has some of the keys (Chlotz: name, display and LED settings; Stägge, Chlötzli, Chischtli and Gitta:
name and LED settings; hello: name, ring and bow brightness, reverse LEDs).
The device reports the ones it has; the web page shows only those.

The device has the values (NVS, set on the device or here). The backend has:

- "settings": the values from the last device report ({key: value}, see KEYS) and the options of the device
  (brightness options, LED animation names, melody names, sound module, LED test, no firmware update), from the
  first report of each
  stream. The
  melodies and the sound module are for the sounds of the LED config (see leds.py).
- "pending": the changes from the web page ({"seq": n, "values": {key: value}}) that the device has not
  acknowledged yet

Sync:

- Device -> backend: the device reports its values after the stream starts, after each heartbeat and at once after
  each change (on the device or from the backend). The backend keeps them.
- Backend -> device: a change on the web page goes into "pending" (seq + 1). The stream of the device sends the
  pending values as "settings" event {"seq": n, "settings": {key: value}}, at once or when the device connects. The
  device applies and saves them and reports with "ack": n. The backend then removes the pending values (ack >= seq).
- A setting changed on both sides while the device was not connected: the web page value wins (pending is sent when
  the device connects).
- A web change back to the device value removes the pending value.

The device checks the values again; it ignores bad ones (its report then shows the real values, and the ack removes
the pending ones).
"""

import re

from .leds import SOUND_MAX, SOUND_RE

# Setting keys: (key, label, min, max), min/max None: text
KEYS = (
    ("name", "Name", None, None),
    ("dim_timeout", "Dim after [s]", 0, 3600),
    ("dim_level", "Dim level [%]", 0, 100),
    ("saver_timeout", "Screensaver after [s]", 0, 3600),
    ("led_brightness", "LED brightness", 1, 255),
    ("anim_running", "LED animation running", 0, 255),
    ("anim_waiting", "LED animation waiting", 0, 255),
    ("ring_brightness", "Ring brightness", 1, 255),
    ("bow_brightness", "Bow brightness", 1, 255),
    ("led_reverse", "Reverse LEDs", 0, 1),
)

# Device name: max. length in bytes (UTF-8) and chars (as the device)
NAME_MAX = 25
NAME_RE = re.compile("^[A-Za-z0-9 _.\\-\u00a0-\ud7ff\ue000-\U0010ffff]*$")


class SettingsError(Exception):
    """Bad setting value"""


def check_value(key, value, options=None):
    """Check one setting value, returns the clean value. Raises SettingsError.

    options: the options of the device (animation indices must be one of them), None if unknown.
    """
    spec = next((item for item in KEYS if item[0] == key), None)
    if spec is None:
        raise SettingsError("unknown setting {}".format(key))
    _, label, low, high = spec
    if low is None:
        if not isinstance(value, str):
            raise SettingsError("{} must be text".format(label))
        value = value.strip()
        if (NAME_RE.match(value) is None) or (len(value.encode("utf-8")) > NAME_MAX):
            raise SettingsError("{}: max. {} bytes, letters, digits, spaces, - _ .".format(label, NAME_MAX))
        return value
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if (not isinstance(value, int)) or isinstance(value, bool):
        raise SettingsError("{} must be a number".format(label))
    if key.startswith("anim_") and isinstance((options or {}).get(key), list):
        high = len(options[key]) - 1
    if (value < low) or (value > high):
        raise SettingsError("{} must be {}-{}".format(label, low, high))
    return value


def check_settings(values, options=None):
    """Check settings ({key: value}, any subset), returns the clean dict. Raises SettingsError."""
    if not isinstance(values, dict):
        raise SettingsError("settings must be an object")
    return {key: check_value(key, value, options) for key, value in values.items()}


def clean_report(values):
    """Get the good settings of a device report ({key: value}), bad and unknown ones are left out"""
    result = {}
    if not isinstance(values, dict):
        return result
    for key, _, _, _ in KEYS:
        if key in values:
            try:
                result[key] = check_value(key, values[key])
            except SettingsError:
                pass
    return result


def clean_melodies(items):
    """Get melodies and melody_secs of the melodies option, without the bad items"""
    names = []
    secs = {}
    for item in items:
        name, colon, text = (item.partition(":") if isinstance(item, str) else ("", "", ""))
        if (len(name) <= SOUND_MAX) and SOUND_RE.match(name) and ((not colon) or text.isdecimal()):
            names.append(name)
            if colon:
                secs[name] = int(text)
    return ({"melodies": names, "melody_secs": secs} if secs else {"melodies": names})


def clean_options(options):
    """Get the good options of a device report

    *_brightness [[value, name], ...], anim_* [name, ...], melodies ["<name>:<length [s]>", ...] (older firmware: only
    the name), sound_fx: true (has a sound module), led_test: true (has the web LED test), no_ota: true (no firmware
    update, USB only, e.g. Gitta). The result has melodies
    [name, ...] and melody_secs {name: length}.
    """
    result = {}
    if not isinstance(options, dict):
        return result
    for key in ("led_brightness", "ring_brightness", "bow_brightness"):
        bright = options.get(key)
        if isinstance(bright, list):
            result[key] = [[item[0], str(item[1])[:40]] for item in bright
                           if isinstance(item, list) and (len(item) == 2) and isinstance(item[0], int)]
    for key in ("anim_running", "anim_waiting"):
        if isinstance(options.get(key), list):
            result[key] = [str(name)[:40] for name in options[key]]
    if isinstance(options.get("melodies"), list):
        result.update(clean_melodies(options["melodies"]))
    for key in ("sound_fx", "led_test", "no_ota"):
        if options.get(key) is True:
            result[key] = True
    return result
