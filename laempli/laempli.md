# Tschenggins Lämpli: common functions

The functions of all Lämplis: job states on the LEDs, serial shell, WiFi, device ID, backend connection and firmware
update. Each Lämpli has its own docs: [Chlotz](chlotz/chlotz.md), [Hello](hello/hello.md),
[Stägge](staegge/staegge.md), [Chlötzli](chloetzli/chloetzli.md), [Gitta](gitta/gitta.md),
[Chischtli](chischtli/chischtli.md). The backend:
[backend/backend.md](../backend/backend.md).

## LEDs: job status

Each LED shows the status of one job. A job is one thing to watch, from a source. The sources are GitLab and GitHub
(webhooks): a job is a pipeline (workflow run) or a job in it, and "generic": scripts (e.g. cron jobs) set their job
states with curl (see [Generic jobs](../backend/backend.md#generic-jobs-scripts-cron)). The source "test" has the jobs
`test01` to `test10`: set one for an LED, then change its status and result on the backend tab Test, to check how the
device shows a job. A path identifies a job: the source, then the
things in it, from coarse to fine:

| Path                                                            | Job                             |
|-----------------------------------------------------------------|---------------------------------|
| `gitlab`, `core/release-config`, `main`                         | GitLab: main pipeline of a ref  |
| `gitlab`, `core/release-config`, `main`, `validate-config`      | GitLab: job of the main pipeline |
| `gitlab`, `core/release-config`, `main`, `yocto_trigger`        | GitLab: downstream pipeline of a trigger job |
| `gitlab`, `core/release-config`, `main`, `yocto_trigger`, `image` | GitLab: job of the downstream pipeline |
| `github`, `fixposition/fixposition-sdk`, `main`, `ci`           | GitHub: newest run of a workflow on a branch |
| `github`, `fixposition/fixposition-sdk`, `main`, `ci`, `ci-images` | GitHub: job of that workflow |
| `generic`, `nas`, `backup`                                      | Generic: any names (1-7) that a script sets |
| `test`, `test01`                                                | Test: status and result from the tab Test |

Set the job of each LED on the backend web page of the device (see [Backend](../backend/backend.md)). Unused LEDs are
off.

Max. age: each LED can have a max. age, set on the device page (seconds or e.g. `1h`, `1d12h`, `7d`; empty or `0`:
forever, the default). When the last change of the job is older, the job data is outdated: the LED shows no data (status
and result UNKNOWN, white, blinks), the only info line is "outdated", e.g. `no change for 7d`. This also covers a final
event that never comes (e.g. a job that stays RUNNING). The backend checks each minute. Jobs without a time (test jobs
before their first change) are never outdated. The Stägge does not show outdated pipelines (see
[Backend](../backend/backend.md#led-config)). Generic jobs also have their own `timeout` (next update expected), see
[Generic jobs](../backend/backend.md#generic-jobs-scripts-cron).

A job has a status (what it does now) and a result (how its last run ended):

| Result  | Colour | GitLab                                         | GitHub (conclusion)                 |
|---------|--------|------------------------------------------------|-------------------------------------|
| PASS    | green  | success                                        | success                             |
| FAIL    | red    | failed                                         | failure, timed_out, startup_failure |
| WARN    | yellow | passed with warnings, failed allow_failure job | -                                   |
| UNKNOWN | white  | canceled, no result yet                        | cancelled, action_required, stale   |

| Status  | Light                    | GitLab                                   | GitHub                              |
|---------|--------------------------|------------------------------------------|-------------------------------------|
| STOPPED | steady                   | finished, job not in newest pipeline     | completed, job not in newest run    |
| WAITING | nervous (e.g. heartbeat) | created, pending, manual, preparing, ... | requested, queued, pending, waiting |
| RUNNING | breathing (e.g. 3 s)     | running                                  | in_progress                         |
| UNKNOWN | slow blink (1 s on/off)  | no data, or no backend for 5 min         | (same)                              |

Skipped jobs (GitLab skipped and scheduled, GitHub skipped and neutral) do not change the result.

The result comes from the last finished pipeline of that ref (or trigger job, or the last completed run of that
workflow), or from the last finished run of that job (the newest job ID, so a retry or re-run counts). While a new
pipeline waits or runs, the LED animates in the colour of the last result. The start and a broken backend connection
depend on the device (see its docs).

Animations (`fw/src/common/ledanim.cpp`): one list for RUNNING and WAITING, first the calm ones (made for RUNNING), then
the nervous ones (made for WAITING):

| #  | Animation                                          | Default for |
|----|----------------------------------------------------|-------------|
| 0  | cosine 1 s (the first RUNNING one)                 |             |
| 1  | breathe 2 s                                        |             |
| 2  | breathe 3 s                                        |             |
| 3  | breathe 4 s, deep: short peak, long dim part       | RUNNING     |
| 4  | breathe 3 s, rest: in 1.2 s, out 1.2 s, rest 0.6 s |             |
| 5  | cosine 0.3 s (the first WAITING one)               |             |
| 6  | pulse 0.6 s                                        |             |
| 7  | pulse 0.4 s                                        |             |
| 8  | heartbeat: two beats, rest, 1 s                    | WAITING     |
| 9  | flicker (random)                                   |             |
| 10 | flutter (bright, 8 Hz shimmer)                     |             |
| 11 | soft blink 0.5 s                                   |             |

The Chlotz selects them in its settings (see [Chlotz](chlotz/chlotz.md#job-leds)). The Hello has fixed
effects (see [Hello](hello/hello.md)).

## Serial shell

All devices have a serial shell (115200 baud, `fw/.venv/bin/pio device monitor -d fw -e <env>`, type `help`). Serial
lines end with CRLF. Ctrl-C cancels an input. The settings are saved on the device (ESP32: NVS, ESP8266: LittleFS).
Common commands:

| Command                     | Function                                    |
|-----------------------------|---------------------------------------------|
| `status`                    | WiFi, backend, LED state, free heap         |
| `config`                    | Show all settings                           |
| `name [<name>\|-]`          | Device name (25 chars), `-` = none          |
| `wifi list`                 | Configured networks (tried in this order)   |
| `wifi scan`                 | Networks in range                           |
| `wifi add`                  | Add network or change password              |
| `wifi del <n>`              | Delete network                              |
| `wifi move <n> <m>`         | Change order                                |
| `wifi reconnect`            | Start new connect round                     |
| `backend url <url>`         | Backend URL (`http(s)://...`), `-` = none   |
| `backend auth`              | HTTP basic auth (empty user = no auth)      |
| `backend insecure on\|off`  | Certificate check off or on                 |
| `backend reconnect`         | Reconnect now                               |
| `ota`                       | Firmware version and update state           |
| `ota install`               | Install newer firmware from the backend     |
| `melody`                    | Show the built-in melodies (numbered)       |
| `melody <n>\|<name>`        | Play a melody (number or name)              |
| `melody stop`               | Stop the melody                             |

Each device has more commands: the Chlotz `display` and `led` (and a menu on the display for all settings, see
[Chlotz](chlotz/chlotz.md#settings)), the Hello `led` and `sound` (see [Hello](hello/hello.md)), the Stägge, the
Chlötzli, the Chischtli and the Gitta `led` (see [Stägge](staegge/staegge.md#firmware)).

## Melodies

The Chlotz, the Hello, the Chlötzli and the Chischtli have a piezo speaker, the Gitta a buzzer. They have 68 built-in melodies in RTTTL (Ring Tone Text
Transfer Language): 48 from the old firmware, and 20 short ones (about 1 s, a few tones) for job sounds, `Pass...` and
`Fail...`. `fw/src/common/rtttl.cpp` has the melodies, the format specification and the sources of the melodies.
`fw/src/common/melody.cpp` plays them (square wave, `SPEAKER_DUTY` in `config.hpp` sets the volume). Test them with the
shell command `melody` or, on the Chlotz, in Test > Melodies. The Stägge has no speaker (`SPEAKER_PIN` -1): it has no
melodies and no job sounds.

Boot sound: at each start, the devices with a speaker play "FFI" in Morse code (1000 Hz, unit 60 ms: about 1.6 s;
`BOOT_MORSE` in `fw/src/common/melody.hpp`).

### Job sounds

Each LED (job) can play a sound when the job stops. Select them in the LED config on the backend (see
[LED config](../backend/backend.md#led-config)); the default is no sound. The play button next to a sound plays it on
the device. Sound: a melody, or on the Hello the sound
effect (sound module). Events:

| Event       | When the job stops with | Note                                                         |
|-------------|-------------------------|--------------------------------------------------------------|
| Fail        | FAIL or WARN            | Each time                                                    |
| Pass        | PASS                    | Each time                                                    |
| Pass → fail | FAIL or WARN            | The result before was PASS. Plays instead of "Fail".         |
| Fail → pass | PASS                    | The result before was FAIL or WARN. Plays instead of "Pass". |

"Pass → fail" and "Fail → pass" play only if they are set, else "Fail" and "Pass" play. The job stopped: the status
changed from RUNNING or WAITING to STOPPED, or a new update has status STOPPED and the status before was UNKNOWN, or the
result changed. This is the same for generic jobs: a script sets RUNNING at the start and STOPPED with the result at
the end (an update without RUNNING before plays only if the result changes). The first state after the start of
the device plays no sound, and an LED with a new job plays no sound. If more jobs stop at the same time, the device
plays one sound: "Pass → fail" first, then "Fail", "Fail → pass", "Pass"; the lowest LED first. The shell command
`status` shows the sounds of each LED. Details: `fw/src/common/jobsound.cpp`.

## WiFi

Station mode only. Up to 6 networks. The device tries them in list order:

1. Scan for networks
2. Try the configured networks in range, in list order (10 s each)
3. Try the configured networks not found by the scan (they can be hidden)
4. Use the first that connects. If none connects, start again. If the connection drops, start again.

In the terminal, `wifi add` takes a number from the last `wifi scan` or any SSID. Adding an existing SSID changes
its password.

## Firmware name and device ID

There can be different firmwares for different devices (number or type of LEDs, ...). Each one has a name
`laempli-<type>`: the PlatformIO env name. A new firmware gets its own env in `fw/platformio.ini`, its type in
`DEVICE_TYPES` (`backend/laempli/firmware.py`, else the backend refuses it), and its sources in `fw/src/<type>/`, with
its own `config.hpp`.

| Firmware          | Hardware                              | Sources                                       |
|-------------------|---------------------------------------|-----------------------------------------------|
| `laempli-chlotz`  | CYD ESP32-2432S028, WS2801 LEDs       | `fw/src/chlotz/`, `fw/src/ledchain/`, `fw/src/common/` |
| `laempli-hello`   | Wemos D1 mini (ESP8266EX, 4 MB flash) | `fw/src/hello/`, `fw/src/common/`                   |
| `laempli-staegge` | Mini D1 ESP32, WS2801 LED strip       | `fw/src/staegge/`, `fw/src/ledchain/`, `fw/src/common/` |
| `laempli-chloetzli` | ESP8266, 3 WS2801 LEDs              | `fw/src/chloetzli/`, `fw/src/chain8266/`, `fw/src/ledchain/`, `fw/src/common/` |
| `laempli-gitta`   | NINA-W102 (ESP32, 2 MB), 6 WS2801 LEDs | `fw/src/gitta/`, `fw/src/ledchain/`, `fw/src/common/` |
| `laempli-chischtli` | ESP8266, 3 WS2801 LEDs              | `fw/src/chischtli/`, `fw/src/chain8266/`, `fw/src/ledchain/`, `fw/src/common/` |

Code for more than one firmware is in `fw/src/common/`: backend client, WiFi, settings (backend, name), shell, LED
colours and animations (`ledanim.cpp`), melodies, device ID, ESP32/ESP8266 differences (`compat.hpp`, `prefs8266.cpp`,
`castore8266.cpp`). The firmwares with a chain of job LEDs (one LED per job: Chlotz, Stägge, Chlötzli, Chischtli, Gitta) share
`fw/src/ledchain/`: job status on the LEDs (`ledstatus.cpp`), output with current limit and dithering (`ledout.cpp`),
LED settings (`ledsettings.cpp`) and the `led` shell commands (`shellled.cpp`). The code includes the `config.hpp` of
the firmware: the env adds `fw/src/<type>`, `fw/src/common` (and `fw/src/ledchain`) to the include path. Each firmware
adds its shell commands (`shellExt...()`) and its synced settings (`sync...()`, see `fw/src/common/syncsettings.hpp`).
The Chlötzli and the Chischtli have the same code (`fw/src/chain8266/`: main, shell commands, synced settings), only
their `config.hpp` (`fw/src/<type>/`) is different.

The size of the job info per LED (`LED_INFO_MAX`, `LED_PATH_SIZE`) is in the `config.hpp` of each firmware: the
backend client keeps it for all `LED_NUM` LEDs. The Stägge (60 LEDs, no display) keeps no info lines.

Each device has an ID `<type>-<6 hex chars>`: the firmware type and the last 3 bytes of its factory MAC address (e.g.
`chlotz-96c480`). It shows in the serial `status` and `config` (and on the Chlotz logo screen). It is also the WiFi
(DHCP) hostname.

The device name is optional (`name <name>`, `name -` clears it): max. 25 bytes of UTF-8 (ä is 2 bytes), letters,
digits, spaces and `-_.`. It shows in the web interface (it can also change there, see
[Backend](../backend/backend.md), device settings) and on the Chlotz display.

## Backend connection

The device connects to `<url>/ws/device?device=<ID>&fw=<version>&ssid=<WiFi>&ip=<local IP>&rssi=<dBm>&leds=<n>&app=laempli`
and keeps this
WebSocket connection open: the backend sends the events (hello, state, time, settings; sound, led and update from the
buttons of the device page), the device sends its reports (RSSI, uptime, free heap, device settings; the first one also
the CPU name and the heap size) after the hello event, each heartbeat and each settings change. One connection in both
directions, also for HTTPS (one TLS connection). It reconnects after errors (5 s, doubled up to 60 s) and when the
settings change. An event "settings" has changes from the web page; the device applies them and reports at once. An
event "led" starts an LED test (buttons on the device page: colour, rainbow, find an LED or a row, normal); it ends
after 60 s. The report has the test while it runs (`fw/src/common/ledremote.cpp`).

Only a Lämpli backend: the device sends `app=laempli` and takes nothing before a hello with `"server": "laempli"`. A
hello of another server (e.g. the energy monitor, "energymon") closes the connection, the state is "wrong server:
<name>", and the next try waits 60 s. The backend accepts only Lämpli device types (see
[WebSocket](../backend/backend.md#endpoints)).

The reverse proxy must pass WebSocket connections (see [Apache](../backend/backend.md#apache)).

HTTPS: the firmware has the root CA certificates of the Debian package ca-certificates
(`/etc/ssl/certs/ca-certificates.crt`); `tools/gen_ca_bundle.py` converts them at build time. Details: ESP32 in
[Chlotz](chlotz/chlotz.md#backend-connection-and-https), ESP8266 in [Hello](hello/hello.md).

The Lämplis offer the ECDHE-RSA cipher suites first, then the ECDSA ones (`tlsSuites()` in
`fw/src/common/backend.cpp`). The RSA certificate check is much faster on these chips. Measured 2026-11-11 (time of
the TLS connect):

| Lämpli               | ECDSA chain (Let's Encrypt default) | RSA chain |
|----------------------|-------------------------------------|-----------|
| Stägge (ESP32)       | 5.4 s                               | 1.8 s     |
| Chlötzli (ESP8266)   | 6.8 s                               | 1.8 s     |
| Gitta (ESP32 rev 1)  | not measured                        | 2.5 s (2026-11-12) |

Thus the server should have an RSA and an ECDSA certificate (see [Apache](../backend/backend.md#tls-certificates)).
A server with only one of them also works.

## Firmware version and update (OTA)

Version: `<yyyymmddhhmmss>-<git short hash>[-dirty]`, made by `tools/version.py` at build time. A clean work tree
uses the commit time, a work tree with changes uses the build time and `-dirty`. The build copies the firmware to
`data/fw/<name>_<version>.bin` (e.g. `data/fw/laempli-chlotz_20261023012737-9e085f6.bin`) and keeps the newest 10 files
of each name.

The backend serves the files in `<data dir>/fw/` (copy them to the server, see [Deploy](../backend/backend.md#deploy)).
When a device connects, the hello event has the newest file for its type, and the device compares the timestamps. If the
backend has a newer firmware, the device does not install it by itself. On the backend, the Devices tab shows an old
firmware in red. The card Device on the device page has the running version and the button "Update firmware", with
the newer version next to it (else "Firmware is up to date"). The button starts the update on a connected device
(editors). The shell command `ota install` also starts it, and the Chlotz also asks (see
[Chlotz](chlotz/chlotz.md#firmware-update-ota)).

The device gets the file on its WebSocket connection to the backend, in parts (no second connection, thus less
heap). It asks for each part, writes it to the flash and tells the backend how many bytes it wrote. At the end it
checks the MD5 and restarts. While the update runs, the job LEDs show noise: a random colour on each LED, new each
40 ms, as the static noise of a TV (`ledNoiseRender()` in `fw/src/common/ledanim.cpp`). The device page shows a
progress bar instead of the button, then "Restarting with <version>". If the update fails, the button comes back with
the error text next to it (e.g. "Update failed: timeout"); the device also reports the error until it restarts. Part
size and number of parts asked ahead: `BACKEND_OTA_CHUNK` and `BACKEND_OTA_WINDOW` in `fw/src/<type>/config.hpp`.
Protocol: `fw/src/common/backend.cpp` and `backend/laempli/wsserver.py`. Firmware older than 2026-11-07 downloads
`/api/firmware/<file>` over HTTPS instead (no progress on the web page).

The Gitta has no OTA (2 MB flash, one app partition, see [Gitta](gitta/gitta.md#flash-memory-no-ota)): its first report
has the option `no_ota`, the device page shows "Firmware update only over USB" instead of the button, and the backend
refuses the update. Flash it over USB.

## Build and flash

PlatformIO is installed in a local venv; the toolchain goes to `fw/.pio-core`:

```sh
python3 -m venv fw/.venv
fw/.venv/bin/pip install platformio
fw/.venv/bin/pio run -d fw -e <env> -t upload     # build and flash (upload_port in fw/platformio.ini)
fw/.venv/bin/pio device monitor -d fw -e <env>    # serial terminal
```

- Change the configuration (pins, number of LEDs, brightness, defaults, ...) in `fw/src/<type>/config.hpp`.
- The build needs the Debian package ca-certificates (CA bundle, see [Backend connection](#backend-connection)).
