# Tschenggins Lämpli "Hello"

![hello](hello.jpg)

Firmware `laempli-hello`, sources `fw/src/hello/` and `fw/src/common/`, configuration `fw/src/hello/config.hpp`. The
functions of all Lämplis (jobs, serial shell, WiFi, backend, OTA) are in [laempli.md](../laempli.md).

Files in this directory: `hello.jpg` (photo), `wmos-d1-mini-*` (D1 mini pictures and schematic).

## Hardware

"Hello" (`CFG_MODEL_HELLO` in the old firmware [TschengginsLaempli](https://github.com/phkehl/TschengginsLaempli)):
a Wemos D1 mini (ESP8266EX, 4 MB flash) in a cylinder (55 mm), on `/dev/ttyUSB5`.

| Part         | Connection                                  | Notes                                               |
|--------------|---------------------------------------------|-----------------------------------------------------|
| LED ring     | WS2801, clock GPIO14 (D5), data GPIO13 (D7) | 2x6 LEDs (12 positions at 30 deg), colour order RBG |
| Bow LED      | 13th LED of the chain                       |                                                     |
| Sound module | GPIO5 (D1)                                  | High pulse (2 s) plays the sound, see [Job sounds]  |
| Onboard LED  | GPIO2 (D4), low active                      | Blinks until the backend is connected               |
| Piezo        | GPIO4 (D2)                                  | Melodies, see [Melodies](../laempli.md#melodies)    |

The two strips go round the cylinder, strip B is offset by half an LED distance.

[Job sounds]: ../laempli.md#job-sounds

## LED effects

For the backend the Hello has one LED (one job, configure LED 1 on the device page). The effects are fixed (no
settings), the colours and animations as on the Chlotz (`fw/src/common/ledanim.cpp`, `fw/src/hello/leds.cpp`):

| Status  | Ring (result colour: green, red, yellow, white) | Bow (white)              |
|---------|-------------------------------------------------|--------------------------|
| STOPPED | Steady                                          | Off                      |
| RUNNING | A light goes round (2 s for one turn)           | Breathe 3 s, rest        |
| WAITING | A light goes round (2 s for one turn)           | Heartbeat                |
| UNKNOWN | Steady                                          | Blink (1 s on, 1 s off)  |

Before the first state from the backend, the status and result are UNKNOWN. LED 1 not configured: all LEDs off.

## Setup and serial commands

First setup in a serial terminal (`fw/.venv/bin/pio device monitor -d fw -p /dev/ttyUSB5`): `wifi scan`, `wifi add`,
`backend url https://...`, `backend auth`, optional `name <name>`. Settings are files in LittleFS (formatted at the
first start). Synced settings (web page or serial terminal): the name, the ring brightness and the bow brightness
(1-255, default 128, options as the Chlotz LED brightness), and reverse LEDs. The brightness scales all LED output of
the ring or the bow. Reverse LEDs: the light spot goes round the other way.

Serial commands of the Hello (with the common ones, see [Serial shell](../laempli.md#serial-shell)):

| Command                         | Function                                                                  |
|---------------------------------|---------------------------------------------------------------------------|
| `led <n>`                       | Light only LED n (0-11 ring, 12 bow)                                      |
| `led colour red\|green\|blue`   | All LEDs one colour (colour order test)                                   |
| `led state <result> <status>`   | Test state, e.g. `led state pass running`                                 |
| `led normal`                    | Job state from the backend again                                          |
| `led brightness [ring\|bow <n>]` | Show options and settings, or set ring or bow brightness (1-255)          |
| `led reverse [on\|off]`         | Show or set reverse LEDs: the light spot goes round the other way         |
| `sound`                         | Trigger the sound module                                                  |

## HTTPS and OTA on the ESP8266

The ESP8266 has much less RAM than the ESP32 (about 32 kB free heap with WiFi). HTTPS works with these limits:

- BearSSL (TLS 1.2 only). The CA certificates (Debian ca-certificates) are in flash: `tools/gen_ca_bundle.py` writes
  `fw/.pio/ca_store/ca_store.h` at build time, `fw/src/common/castore8266.cpp` gives BearSSL the one it asks for.
- The server has no max. fragment length, so BearSSL must hold a full TLS record (up to the buffer size - 5 bytes).
  The receive buffer is 4.5 kB (`BACKEND_TLS_RX`): the largest handshake record of the production server is the RSA
  certificate chain with 4.1 kB (the ECDSA chain: 3.4 kB), the events of one LED are small.
  The firmware update comes on the stream in parts of 1 kB, 2 parts asked ahead (`BACKEND_OTA_CHUNK`,
  `BACKEND_OTA_WINDOW`), thus the TLS records stay small.
- The TLS buffers are in the second heap in IRAM (MMU option `PIO_FRAMEWORK_ARDUINO_MMU_CACHE16_IRAM48_SECHEAP_SHARED`
  in `fw/platformio.ini`). With the stream and the report connection open (certificate check on), about 18 kB normal
  heap and 10 kB IRAM heap are free. Without this option, the second handshake ran out of memory.
- Both connections use one TLS session (session ID resumption, the server must support it, the production server does):
  only the first handshake has the certificate check and the key exchange. A new URL or insecure flag starts a new
  session. A full handshake on both connections also fits (tested).
- The certificate dates need the time: NTP (`pool.ntp.org`), else the backend time, else the build time. With the
  build time, a certificate newer than the firmware is "not yet valid": the firmware then tries again (NTP has the
  time later), it does not mark the certificate as untrusted.
- A full TLS handshake takes about 0.5 s (160 MHz). The LEDs stop for this time.
- OTA: button "Update firmware" on the device page, or `ota install` (see [Firmware
  update](../laempli.md#firmware-version-and-update-ota)). There is no rollback on the ESP8266: a firmware that does not
  start needs the USB cable.
